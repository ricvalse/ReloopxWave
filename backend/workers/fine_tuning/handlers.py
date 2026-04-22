"""Fine-tuning pipeline (section 5.4, weeks 9-10).

Steps: collect -> anonymize -> quality-filter -> dataset build -> train -> evaluate -> deploy.
Each handler here wraps one step; they can run sequentially or parallel-fanout.
"""
from __future__ import annotations

from shared import get_logger

logger = get_logger(__name__)


async def fine_tune_train(ctx: dict, tenant_id: str, *, dataset_path: str, base_model: str) -> dict:
    """Lifts the pipeline up to the OpenAI FT job submission and polls to completion."""
    logger.info("worker.ft.train.started", tenant_id=tenant_id, base_model=base_model)
    raise NotImplementedError


async def fine_tune_evaluate(ctx: dict, ft_model_id: str, *, test_set_path: str) -> dict:
    """Runs held-out eval, compares FT vs baseline, returns delta metrics."""
    logger.info("worker.ft.evaluate.started", ft_model_id=ft_model_id)
    raise NotImplementedError


async def fine_tune_deploy(ctx: dict, ft_model_row_id: str) -> dict:
    """Registers model in ft_models with is_default flag once eval passes threshold."""
    logger.info("worker.ft.deploy.started", ft_model_row_id=ft_model_row_id)
    raise NotImplementedError
