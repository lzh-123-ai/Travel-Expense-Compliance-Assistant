import json
from pathlib import Path

from app.evaluation.contracts import EvalDataset


def load_eval_dataset(path: Path) -> EvalDataset:
    """从版本控制中的 JSON 加载并严格校验评测标注。"""
    return EvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
