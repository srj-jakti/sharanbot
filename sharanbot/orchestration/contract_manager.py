"""Contract manager — shared interface definitions between team agents.

Manages API specs, DB schemas, component interfaces, and design tokens
that all workers reference. Ensures workers build against the same
interface definitions. Changes go through a formal amendment process.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.orchestration.role_prompts import Role


class ContractType:
    API_SPEC = "api_spec"
    DB_SCHEMA = "db_schema"
    COMPONENT_INTERFACES = "component_interfaces"
    DESIGN_TOKENS = "design_tokens"
    AUTH_SPEC = "auth_spec"
    EVENT_SCHEMA = "event_schema"


class AmendmentStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class Amendment:
    id: str = field(default_factory=lambda: f"amend-{uuid.uuid4().hex[:8]}")
    contract_name: str = ""
    proposed_change: str = ""
    requester_worker_id: str = ""
    status: str = AmendmentStatus.PENDING
    timestamp: float = field(default_factory=time.time)
    review_notes: str = ""


@dataclass
class Contract:
    name: str
    content: str
    contract_type: str
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    amendments: list[Amendment] = field(default_factory=list)


# Which roles see which contract types
_ROLE_CONTRACT_MAP: dict[Role, list[str]] = {
    Role.SOLUTION_ARCHITECT: [
        ContractType.API_SPEC, ContractType.DB_SCHEMA, ContractType.COMPONENT_INTERFACES,
        ContractType.DESIGN_TOKENS, ContractType.AUTH_SPEC, ContractType.EVENT_SCHEMA,
    ],
    Role.UI_COMPONENT_DEV: [ContractType.API_SPEC, ContractType.COMPONENT_INTERFACES, ContractType.DESIGN_TOKENS],
    Role.FRONTEND_LOGIC_DEV: [ContractType.API_SPEC, ContractType.COMPONENT_INTERFACES, ContractType.DESIGN_TOKENS],
    Role.RESPONSIVE_MOBILE_SPECIALIST: [ContractType.API_SPEC, ContractType.COMPONENT_INTERFACES],
    Role.API_DEV: [ContractType.API_SPEC, ContractType.DB_SCHEMA, ContractType.AUTH_SPEC],
    Role.DATABASE_DEV: [ContractType.DB_SCHEMA],
    Role.AUTH_SECURITY_DEV: [ContractType.API_SPEC, ContractType.AUTH_SPEC],
    Role.BUSINESS_LOGIC_DEV: [ContractType.API_SPEC, ContractType.DB_SCHEMA, ContractType.EVENT_SCHEMA],
    Role.INTEGRATION_DEV: [ContractType.API_SPEC, ContractType.EVENT_SCHEMA],
    Role.QA_UNIT_TESTER: [ContractType.API_SPEC, ContractType.DB_SCHEMA],
    Role.QA_INTEGRATION_TESTER: [ContractType.API_SPEC, ContractType.DB_SCHEMA],
    Role.QA_E2E_TESTER: [ContractType.API_SPEC],
    Role.SECURITY_TESTER: [ContractType.API_SPEC, ContractType.AUTH_SPEC],
    Role.SENIOR_CODE_REVIEWER: [
        ContractType.API_SPEC, ContractType.DB_SCHEMA, ContractType.COMPONENT_INTERFACES,
    ],
}


class ContractManager:
    """Manages shared contract artifacts for cross-team coordination."""

    def __init__(self, workspace: Path | None = None):
        self._contracts: dict[str, Contract] = {}
        self._workspace = workspace

    def store_contract(self, name: str, content: str,
                       contract_type: str = ContractType.API_SPEC) -> None:
        """Store or update a contract."""
        existing = self._contracts.get(name)
        if existing:
            existing.content = content
            existing.version += 1
            existing.updated_at = time.time()
        else:
            self._contracts[name] = Contract(
                name=name, content=content, contract_type=contract_type,
            )

    def get_contract(self, name: str) -> str | None:
        contract = self._contracts.get(name)
        return contract.content if contract else None

    def get_contracts_for_role(self, role: Role) -> dict[str, str]:
        """Get only contracts relevant to a specific role."""
        allowed_types = _ROLE_CONTRACT_MAP.get(role, [])
        if not allowed_types:
            # Roles not in the map see no contracts by default
            return {}

        result = {}
        for name, contract in self._contracts.items():
            if contract.contract_type in allowed_types:
                result[name] = contract.content
        return result

    def get_contracts_context(self, role: Role) -> str:
        """Get formatted contracts for prompt injection."""
        contracts = self.get_contracts_for_role(role)
        if not contracts:
            return "No contracts assigned to your role."
        lines = ["## Shared Contracts"]
        for name, content in contracts.items():
            lines.append(f"\n### {name}\n```\n{content[:2000]}\n```")
        return "\n".join(lines)

    def propose_amendment(self, contract_name: str, proposed_change: str,
                          requester_worker_id: str = "") -> Amendment | None:
        """Propose a change to a contract."""
        if contract_name not in self._contracts:
            return None
        amendment = Amendment(
            contract_name=contract_name,
            proposed_change=proposed_change[:1000],
            requester_worker_id=requester_worker_id,
        )
        self._contracts[contract_name].amendments.append(amendment)
        return amendment

    def approve_amendment(self, amendment_id: str) -> bool:
        """Approve and apply a contract amendment."""
        for contract in self._contracts.values():
            for amendment in contract.amendments:
                if amendment.id == amendment_id and amendment.status == AmendmentStatus.PENDING:
                    amendment.status = AmendmentStatus.APPROVED
                    contract.content += f"\n\n--- Amendment ({amendment.id}) ---\n{amendment.proposed_change}"
                    contract.version += 1
                    contract.updated_at = time.time()
                    return True
        return False

    def reject_amendment(self, amendment_id: str, reason: str = "") -> bool:
        for contract in self._contracts.values():
            for amendment in contract.amendments:
                if amendment.id == amendment_id and amendment.status == AmendmentStatus.PENDING:
                    amendment.status = AmendmentStatus.REJECTED
                    amendment.review_notes = reason
                    return True
        return False

    def save(self, path: Path | None = None) -> None:
        target = path or (self._workspace / ".sharanbot" / "contracts.json" if self._workspace else None)
        if not target:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for name, contract in self._contracts.items():
                d = asdict(contract)
                data[name] = d
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save contracts: {}", e)

    @classmethod
    def load(cls, path: Path) -> ContractManager:
        mgr = cls()
        if not path.exists():
            return mgr
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for name, cdata in data.items():
                amendments = [Amendment(**a) for a in cdata.pop("amendments", [])]
                contract = Contract(**cdata)
                contract.amendments = amendments
                mgr._contracts[name] = contract
        except Exception as e:
            logger.warning("Failed to load contracts: {}", e)
        return mgr

    def list_contracts(self) -> list[str]:
        return list(self._contracts.keys())
