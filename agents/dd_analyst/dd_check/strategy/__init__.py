"""策略解析：按 busCode × custType × phase 匹配模板，得到启用检查维度。

节点 resolve_strategy 调用 StrategyResolver.resolve(...)。
模板默认来自 strategy/templates/default.json，可用 DD_CHECK_STRATEGY_DIR 覆盖。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..models import CustType, Phase


@dataclass
class Strategy:
    """一份检查策略：维度列表 + 匹配条件；对公可追加 corporate_extra。"""

    id: str
    description: str = ""
    dimensions: List[str] = field(default_factory=list)
    match_bus: List[str] = field(default_factory=lambda: ["*"])
    match_cust: List[str] = field(default_factory=lambda: ["PRIVATE", "CORPORATE", "OTHER"])
    match_phase: List[str] = field(default_factory=lambda: ["CHECK"])
    corporate_extra: List[str] = field(default_factory=list)

    def enabled_for(self, cust: CustType) -> List[str]:
        """按客户类型裁剪维度（对私去掉受益所有人等）。"""
        dims = list(self.dimensions)
        if cust == CustType.CORPORATE:
            for d in self.corporate_extra:
                if d not in dims:
                    dims.append(d)
        elif cust != CustType.CORPORATE:
            dims = [d for d in dims if d != "beneficial_owner"]
        return dims


def _builtin_template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "default.json"


def _load_strategy_file(path: Path) -> Dict[str, Any]:
    """加载 JSON/YAML 策略文件。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # optional
        except ImportError as exc:
            raise RuntimeError(
                f"PyYAML required to load {path.name}; use JSON templates or pip install PyYAML"
            ) from exc
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"strategy file must be an object: {path}")
    return data


class StrategyResolver:
    """加载策略库并做匹配打分，选出最优 Strategy。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._strategies: List[Strategy] = []
        self.reload()

    def reload(self) -> None:
        """从目录或内置 default.json 重新加载策略。"""
        paths: List[Path] = []
        if self.settings.strategy_dir:
            root = Path(self.settings.strategy_dir)
            if root.is_dir():
                paths.extend(sorted(root.glob("*.json")))
                paths.extend(sorted(root.glob("*.yaml")))
                paths.extend(sorted(root.glob("*.yml")))
        if not paths:
            paths = [_builtin_template_path()]
        strategies: List[Strategy] = []
        for path in paths:
            data = _load_strategy_file(path)
            for sid, raw in data.items():
                if not isinstance(raw, dict):
                    continue
                match = raw.get("match") or {}
                strategies.append(
                    Strategy(
                        id=str(sid),
                        description=str(raw.get("description") or ""),
                        dimensions=list(raw.get("dimensions") or []),
                        match_bus=[str(x) for x in (match.get("busCode") or ["*"])],
                        match_cust=[
                            str(x)
                            for x in (match.get("custType") or ["PRIVATE", "CORPORATE", "OTHER"])
                        ],
                        match_phase=[str(x) for x in (match.get("phase") or ["CHECK"])],
                        corporate_extra=list(raw.get("corporate_extra") or []),
                    )
                )
        self._strategies = strategies

    def normalize_cust_type(self, raw: str) -> CustType:
        """业务侧客户类型字符串 → 枚举（可走 cust_type_map）。"""
        key = (raw or "").strip()
        mapped = self.settings.cust_type_map.get(key) or self.settings.cust_type_map.get(key.lower())
        if mapped:
            try:
                return CustType(mapped)
            except ValueError:
                pass
        upper = key.upper()
        try:
            return CustType(upper)
        except ValueError:
            return CustType.OTHER

    def normalize_phase(self, raw: str) -> Phase:
        """阶段字符串 → CHECK / RECHECK 等。"""
        try:
            return Phase((raw or "CHECK").strip().upper())
        except ValueError:
            return Phase.CHECK

    def resolve(self, bus_code: str, cust_type: CustType, phase: Phase) -> Strategy:
        """匹配打分选策略；无命中则返回内置 fallback 维度集。"""
        bus = (bus_code or "").strip()
        scored: List[tuple[int, Strategy]] = []
        for s in self._strategies:
            if str(cust_type.value) not in s.match_cust and "*" not in s.match_cust:
                continue
            if str(phase.value) not in s.match_phase and "*" not in s.match_phase:
                continue
            bus_ok = "*" in s.match_bus or bus in s.match_bus or any(
                bus.upper() == b.upper() for b in s.match_bus if b != "*"
            )
            if not bus_ok:
                continue
            score = 0
            if "*" not in s.match_bus and bus_ok:
                score += 10
            if str(phase.value) in s.match_phase:
                score += 5
            scored.append((score, s))
        if not scored:
            return Strategy(
                id="fallback",
                description="Fallback strategy",
                dimensions=[
                    "writing",
                    "basic_info_completeness",
                    "id_validity",
                    "checkbox_consistency",
                    "logic_consistency",
                ],
                corporate_extra=["beneficial_owner"],
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
