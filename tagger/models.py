"""Classic candidates the Kev models are compared against."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from .config import Dataset


class ConstantPrevalence:
    """The training positive rate for every row: the floor any model must beat."""

    name = "constant_rate"

    def __init__(self, ds: Dataset):
        self.ds = ds

    def fit(self, df: pd.DataFrame) -> "ConstantPrevalence":
        self.p = float(df["label"].mean())
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return np.full(len(df), self.p)


class LookupBaseline:
    """Training positive rate per value of one column (deciles if numeric).

    Unseen or missing values fall back to the overall training rate.
    """

    name = "lookup"

    def __init__(self, ds: Dataset):
        self.ds, self.col = ds, ds.lookup
        self.numeric = ds.lookup in ds.numeric

    def _keys(self, df: pd.DataFrame) -> pd.Series:
        if not self.numeric:
            return df[self.col]
        values = df[self.col].to_numpy(dtype=float)
        keys = np.searchsorted(self.edges, values, side="right").astype(float)
        keys[np.isnan(values)] = np.nan
        return pd.Series(keys, index=df.index)

    def fit(self, df: pd.DataFrame) -> "LookupBaseline":
        self.prior = float(df["label"].mean())
        if self.numeric:
            finite = df[self.col].dropna().to_numpy(dtype=float)
            self.edges = np.unique(np.quantile(finite, np.linspace(0, 1, 11)[1:-1])) if len(finite) else np.array([])
        self.rates = df.groupby(self._keys(df))["label"].mean().to_dict()
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self._keys(df).map(self.rates).fillna(self.prior).to_numpy(dtype=float)


class SklearnCandidate:
    """Shared fit/predict for pipelines over the configured features."""

    def __init__(self, ds: Dataset):
        self.ds = ds

    def fit(self, df: pd.DataFrame):
        self.pipeline = self.build()
        self.pipeline.fit(df[self.ds.features], df["label"])
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        return self.pipeline.predict_proba(df[self.ds.features])[:, 1]


class LogRegModel(SklearnCandidate):
    """Scaled numeric inputs plus one-hot categories, L2-regularised logistic regression."""

    name = "logreg"

    def build(self):
        parts = []
        if self.ds.numeric:
            parts.append(("num", make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                                               StandardScaler()), list(self.ds.numeric)))
        if self.ds.categorical:
            parts.append(("cat", OneHotEncoder(handle_unknown="ignore"), list(self.ds.categorical)))
        return make_pipeline(ColumnTransformer(parts), LogisticRegression(max_iter=2000))


class BoostedTreesModel(SklearnCandidate):
    """Histogram gradient boosting; learns non-linear effects and interactions.

    Fixed settings, no tuning on eval. Early stopping uses a slice of train only.
    """

    name = "boosted_trees"

    def build(self):
        parts = [("num", "passthrough", list(self.ds.numeric))] if self.ds.numeric else []
        if self.ds.categorical:
            parts.append(("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
                          list(self.ds.categorical)))
        mask = [False] * len(self.ds.numeric) + [True] * len(self.ds.categorical)
        return make_pipeline(ColumnTransformer(parts), HistGradientBoostingClassifier(
            categorical_features=mask, learning_rate=0.1, max_iter=500,
            early_stopping=True, validation_fraction=0.1, random_state=0))


CANDIDATES = {c.name: c for c in (ConstantPrevalence, LookupBaseline, LogRegModel, BoostedTreesModel)}
