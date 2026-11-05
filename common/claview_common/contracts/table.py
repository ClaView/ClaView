"""Table identity shared by module contracts."""

from pydantic import BaseModel


class TableRef(BaseModel):
    org_id: str
    table_id: str
    table_name: str | None = None
