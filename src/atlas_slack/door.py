"""Library face — `Door(profile_name, manifest)` for jobs and scripts (A6).

Same policy engine and journal as the MCP server; different meaning of
`needs-human`: the library has no decision adapter in this slice, so a send
that needs a human is **refused** (`no_decision_adapter`) — never auto-allowed.

    from atlas_slack.door import Door
    door = Door("acme", manifest={"cls": "C2", "allowed_methods": [...],
                                  "allowed_targets": [...], "read_scope": {"channels": [...]}})
    res = door.call("conversations.history", {"channel": "C1", "limit": 5})
    res.verdict.decision   # allow | needs-human | refuse
    res.data               # Slack response dict (None when refused)

Executed methods in this slice: reads (row 0) and `chat.postMessage` through
the core. Every other write refuses `not_in_slice` here — the transitional
path exists only behind the interactive hook.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas_slack import core, shaping
from atlas_slack.policy import ALLOW, REFUSE, Verdict, classify, evaluate, load_policy
from atlas_slack.profile import Profile, load_profile


@dataclass
class DoorResult:
    verdict: Verdict
    data: dict | None = None
    op_id: str | None = None
    error: str | None = None       # Slack error / unknown outcome after the call was made
    comm_id: str | None = None     # the caller's, or minted by the door

    @property
    def ok(self) -> bool:
        return self.data is not None and self.error is None


class _SdkTransport:
    """Default transport — slack_sdk WebClient under the profile's token env vars."""

    def __init__(self, profile: Profile):
        self.profile = profile
        self._clients = {}

    def __call__(self, method: str, params: dict, as_bot: bool) -> dict:
        import os

        from slack_sdk import WebClient
        var = self.profile.token_var(as_bot)
        if var not in self._clients:
            token = os.environ.get(var)
            if not token:
                raise ValueError(f"{var} environment variable is required")
            self._clients[var] = WebClient(token=token)
        resp = self._clients[var].api_call(method, params=shaping.encode_nested(params))
        return resp.data if hasattr(resp, "data") else dict(resp)


class Door:
    def __init__(self, profile_name: str | None = None, manifest: dict | None = None, transport=None,
                 profile: Profile | None = None):
        self.profile = profile or load_profile(profile_name)
        self.manifest = manifest
        self.transport = transport or _SdkTransport(self.profile)

    # ------------------------------------------------------------------ calls
    def verdict(self, method: str, params: dict | None = None, as_bot: bool = False,
                provenance: dict | None = None) -> Verdict:
        v, _ = core.evaluate_with_journal(method, params or {}, as_bot, provenance, self.profile,
                                          face="library", manifest=self.manifest, record_sighting=False)
        return v

    def call(self, method: str, params: dict | None = None, as_bot: bool = False,
             provenance: dict | None = None) -> DoorResult:
        params = dict(params or {})
        eff = classify(method, params)
        if eff.route == "local":
            v = evaluate(method, params, as_bot, provenance, self.profile, load_policy(self.profile), face="library")
            if method == "_comm_id":
                if v.decision == REFUSE:
                    return DoorResult(v)
                from atlas_slack.provenance import mint_comm_id
                try:
                    j = core.open_journal(self.profile.journal_dir)
                except core.JournalUnavailable as e:
                    v.decision, v.reason, v.message = REFUSE, "journal_unavailable", str(e)
                    return DoorResult(v)
                cid = mint_comm_id(lambda c: j.comm_first_seen(c) is not None or j.has_comm(c))
                j.note_comm_seen(cid)
                return DoorResult(v, data={"ok": True, "comm_id": cid}, comm_id=cid)
            return DoorResult(v, data={"ok": True, "rights": self.rights()})
        if eff.route == "core":
            try:
                res = core.execute_send(method, params, as_bot, provenance, profile=self.profile,
                                        transport=self.transport, face="library", manifest=self.manifest,
                                        caller=(provenance or {}).get("prepared_by") or "door")
            except core.Refused as e:
                return DoorResult(e.verdict)
            except core.SendFailed as e:
                return DoorResult(e.verdict, op_id=e.op_id, error=f"{e.code} ({e.outcome})")
            return DoorResult(res.verdict, data=res.data, op_id=res.op_id, comm_id=res.comm_id)

        v = evaluate(method, params, as_bot, provenance, self.profile, load_policy(self.profile),
                     face="library", manifest=self.manifest)
        if v.decision != ALLOW:
            if v.decision != REFUSE:
                v.decision, v.reason = REFUSE, "no_decision_adapter"
            core.record_call(self.profile, method, "refused", as_bot=as_bot, caller="door", error_code=v.reason)
            return DoorResult(v)
        try:
            data = self.transport(method, params, as_bot)
        except Exception as e:
            core.record_call(self.profile, method, "error", as_bot=as_bot, caller="door", error_code="exception")
            return DoorResult(v, error=f"exception: {e}")
        status = "ok" if data.get("ok", True) else "error"
        core.record_call(self.profile, method, status, as_bot=as_bot, caller="door",
                         error_code=None if status == "ok" else data.get("error"))
        return DoorResult(v, data=data, error=None if status == "ok" else data.get("error"))

    def rights(self) -> str:
        from atlas_slack.gate import rights
        return rights(self.profile)

    # ----------------------------------------------------------------- doctor
    def doctor(self, auth_test: dict, as_bot: bool) -> list[str]:
        """Compare an `auth.test` response (passed in, not fetched) with the profile.

        Returns a list of mismatches; empty = consistent. A bot token from
        another workspace, or a user token of another person, must never be
        used under this profile (Strategy п. 17).
        """
        p = self.profile
        problems = []
        if not isinstance(auth_test, dict) or not auth_test.get("ok", True):
            return [f"auth.test failed: {(auth_test or {}).get('error', 'no response')}"]
        team = auth_test.get("team_id")
        if p.workspace_id and team != p.workspace_id:
            problems.append(f"workspace: token belongs to {team}, profile says {p.workspace_id}")
        if not p.workspace_id:
            problems.append("workspace_id is not set in the profile — cannot check the token's workspace")
        uid = auth_test.get("user_id")
        if as_bot:
            if p.bot_user_id and uid != p.bot_user_id:
                problems.append(f"bot: token's user_id {uid} ≠ profile bot_user_id {p.bot_user_id}")
            if not auth_test.get("bot_id"):
                problems.append("bot: auth.test has no bot_id — this is not a bot token")
            if not p.bot_user_id:
                problems.append("bot_user_id is not set — person-voice sends cannot be attributed")
        else:
            if p.person_user_id and uid != p.person_user_id:
                problems.append(f"person: token's user_id {uid} ≠ profile person_user_id {p.person_user_id}")
            if auth_test.get("bot_id"):
                problems.append("person: auth.test reports a bot_id — a bot token sits in the user-token slot")
            if not p.person_user_id:
                problems.append("person_user_id is not set — cannot check whose user token this is")
        return problems
