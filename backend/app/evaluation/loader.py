"""从 JSON 文件加载并校验版本化评测数据集。"""

import json
from pathlib import Path

from app.evaluation.contracts import EvalDataset, RetrievalEvalDataset


def load_eval_dataset(path: Path) -> EvalDataset:
    """从版本控制中的 JSON 加载并严格校验评测标注。"""
    return EvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_retrieval_eval_dataset(path: Path) -> RetrievalEvalDataset:
    """加载 Stage 9 的版本、日期和权限感知检索标注。"""
    return RetrievalEvalDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
