"""Compute index results from explicitly supplied standardized scores and weights."""
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


DIMENSIONS = {f"D{i + 1}": tuple(f"I{j + 1:02d}" for j in range(i * 3, i * 3 + 3)) for i in range(4)}
INDICATORS = frozenset(code for codes in DIMENSIONS.values() for code in codes)
VERSION = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
STOCK_CODE = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
YEAR = Annotated[int, Field(strict=True, ge=1990, le=2100)]
SCORE = Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]
WEIGHT = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
MAX_FILE_BYTES = 32 * 1024 * 1024


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyScores(InputModel):
    stock_code: STOCK_CODE
    report_year: YEAR
    indicators: dict[str, SCORE] = Field(min_length=12, max_length=12)


class Dataset(InputModel):
    version: VERSION
    rows: list[CompanyScores] = Field(min_length=1)


class Weights(InputModel):
    version: VERSION
    dataset_version: VERSION
    weights: dict[str, WEIGHT] = Field(min_length=12, max_length=12)


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def load_file(path, schema):
    with path.open("rb") as file:
        raw = file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("measurement input exceeds size limit")
    obj = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)
    return schema.model_validate(obj), sha256(raw).hexdigest()


class Measurement:
    def __init__(self, directory):
        self.files = {"dataset": Path(directory) / "indicators.json", "weights": Path(directory) / "weights.json"}

    def _load(self):
        missing, loaded = [], {}
        for key, schema in (("dataset", Dataset), ("weights", Weights)):
            try:
                loaded[key] = load_file(self.files[key], schema)
            except FileNotFoundError:
                missing.append(key)
            except (OSError, RecursionError, ValueError):
                return {"status": "inputs_invalid", "issue": key}, {}, {}
        if missing:
            return {"status": "inputs_missing", "missing": missing}, {}, {}
        dataset, data_hash = loaded["dataset"]
        params, params_hash = loaded["weights"]
        weights = params.weights
        if params.dataset_version != dataset.version:
            return {"status": "inputs_invalid", "issue": "dataset_version_mismatch"}, {}, {}
        if (weights.keys() != INDICATORS or not math.isclose(math.fsum(weights.values()), 1, abs_tol=1e-6)
                or any(not any(weights[code] for code in codes) for codes in DIMENSIONS.values())):
            return {"status": "inputs_invalid", "issue": "weights"}, {}, {}
        rows = {(row.stock_code, row.report_year): row.indicators for row in dataset.rows}
        if len(rows) != len(dataset.rows) or any(values.keys() != INDICATORS for values in rows.values()):
            return {"status": "inputs_invalid", "issue": "dataset"}, {}, {}
        return {"status": "ready", "dataset_version": dataset.version, "weights_version": params.version,
                "dataset_sha256": data_hash, "weights_sha256": params_hash,
                "years": sorted({year for _, year in rows})}, rows, weights

    def status(self):
        return self._load()[0]

    def result(self, stock_code, report_year):
        state, rows, weights = self._load()
        if state["status"] != "ready":
            return state | {"result": None}
        values = rows.get((stock_code, report_year))
        if values is None:
            return state | {"status": "not_found", "result": None}
        contributions = {code: values[code] * weights[code] for codes in DIMENSIONS.values() for code in codes}
        dimensions = {dim: math.fsum(contributions[code] for code in codes) / math.fsum(weights[code] for code in codes)
                      for dim, codes in DIMENSIONS.items()}
        score = math.fsum(contributions.values()) / math.fsum(weights.values())
        return state | {"result": {"stock_code": stock_code, "report_year": report_year, "score": score,
            "dimensions": dimensions, "indicator_scores": values, "weights": weights}}
