"""Unique values for each session, so runs never pass on data left by earlier runs."""

from __future__ import annotations

import secrets

from pydantic import BaseModel


class TestData(BaseModel):
    __test__ = False  # not a pytest class

    tag: str
    first_name: str
    last_name: str
    full_name: str
    email: str
    company: str
    phone: str
    note: str

    @classmethod
    def generate(cls, tag: str | None = None) -> TestData:
        tag = tag or f"qa{secrets.token_hex(3)}"
        return cls(
            tag=tag,
            first_name="Test",
            last_name=f"User-{tag}",
            full_name=f"Test User-{tag}",
            email=f"test+{tag}@example.com",
            company=f"QA Company {tag}",
            phone="555-0100",
            note=f"Automated test {tag}",
        )

    def lines(self) -> str:
        return "\n".join(f"  {k}: {v}" for k, v in self.model_dump().items() if k != "tag")
