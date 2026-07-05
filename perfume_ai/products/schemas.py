from pydantic import BaseModel


class IntentSchema(BaseModel):
    gender: str | None = None
    season: str | None = None
    occasion: str | None = None
    max_price: float | None = None
    similar_to: str | None = None
    longevity: str | None = None