"""Roles (Entra ID app roles) and what each may do.

Create app roles with these exact values on the app registration
(Deployment/scripts/setup_entra.sh does it) and assign users or groups to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ADMIN, SUPERVISOR, QA, AGENT = "Admin", "Supervisor", "QA", "Agent"
ALL_ROLES = (ADMIN, SUPERVISOR, QA, AGENT)

PERMISSIONS = {
    "view_all_calls": {ADMIN, SUPERVISOR, QA},
    "view_insights": {ADMIN, SUPERVISOR, QA},
    "view_agents": {ADMIN, SUPERVISOR, QA},
    "manage_pipeline": {ADMIN, SUPERVISOR},
    "edit_settings": {ADMIN},
}


@dataclass
class User:
    id: str
    name: str
    email: str
    roles: list[str] = field(default_factory=list)
    agent_id: str = ""
    is_authenticated: bool = True

    @property
    def primary_role(self) -> str:
        for role in ALL_ROLES:
            if role in self.roles:
                return role
        return ""

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def can(self, permission: str) -> bool:
        return bool(PERMISSIONS.get(permission, set()) & set(self.roles))

    def to_session(self) -> dict:
        return {"id": self.id, "name": self.name, "email": self.email, "roles": self.roles, "agent_id": self.agent_id}

    @classmethod
    def from_session(cls, data: dict) -> "User":
        return cls(id=data["id"], name=data["name"], email=data.get("email", ""), roles=list(data.get("roles", [])),
                   agent_id=data.get("agent_id", ""))


class AnonymousUser:
    is_authenticated = False
    roles: list[str] = []
    name = email = agent_id = primary_role = ""

    def can(self, permission: str) -> bool:
        return False
