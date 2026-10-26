"""Explicit local model devices and precision, without a CPU fallback."""


def model_load_options(device: str, dtype: str | None = None) -> dict:
    if dtype not in (None, "float16", "float32", "bfloat16"):
        raise ValueError("model dtype must be float16, float32, or bfloat16")
    if str(device).startswith("cuda") or dtype is not None:
        import torch

        if str(device).startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA was requested but is unavailable. Use the isolated CUDA "
                "environment and a CUDA-enabled PyTorch installation; CPU fallback is disabled."
            )
        if dtype is not None:
            return {"torch_dtype": getattr(torch, dtype)}
    return {}
