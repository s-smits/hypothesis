from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator


class FooBar(BaseModel):
    """A count.

    Args:
        count: The current count.
    """

    kind: Literal["foo_bar"] = "foo_bar"
    count: int


class Baz(BaseModel):
    """A label.

    Args:
        label: The label text.
    """

    kind: Literal["baz"] = "baz"
    label: str


# Add a new shared type to both.
Value = Annotated[FooBar | Baz, Discriminator("kind")]
TYPES: dict[str, type[BaseModel]] = {"foo_bar": FooBar, "baz": Baz}
