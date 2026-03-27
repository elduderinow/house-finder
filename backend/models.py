from pydantic import BaseModel, Field
from typing import Optional


class SearchCriteria(BaseModel):
    transaction: str = "buy"  # "buy" or "rent"
    building_type: Optional[str] = None  # house, apartment, villa, townhouse
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    garden: Optional[str] = None  # "yes", "no", "any"
    sqm_min: Optional[int] = None
    sqm_max: Optional[int] = None
    bedrooms_min: Optional[int] = None
    bedrooms_max: Optional[int] = None
    postcodes: list[str] = Field(default_factory=list)
    enabled_sources: list[str] = Field(default_factory=list)  # empty = all available


class PropertyResult(BaseModel):
    title: str
    price: Optional[int] = None
    price_text: str = ""
    location: str = ""
    postcode: str = ""
    street: Optional[str] = None        # full street + house number, e.g. "Kerkstraat 12"
    link: str = ""
    source: str = ""
    bedrooms: Optional[int] = None
    sqm: Optional[int] = None           # living area m²
    garden: Optional[bool] = None       # has garden
    garden_sqm: Optional[int] = None    # garden surface m²
    image_url: Optional[str] = None
    property_type: Optional[str] = None  # normalised: house, apartment, villa, etc.
    listed_date: Optional[str] = None   # ISO date string: "2026-03-24"
    first_seen: Optional[str] = None    # ISO datetime string: "2026-03-27 14:00:00"
