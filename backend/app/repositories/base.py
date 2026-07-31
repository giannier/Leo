"""Tenant-scoped repository base.

The Constitution requires that every business query filter by `tenant_id` at
the repository layer. Relying on each call site to remember that is how
cross-tenant leaks happen, so the filter is structural here instead:

* `tenant_id` is a constructor argument, not a method parameter. There is no
  way to call a query method without having supplied one.
* Every read is built from `_scoped()`, which applies the filter before the
  caller can touch the statement.
* Writes stamp the tenant themselves and reject any attempt to pass a
  different one.
* A repository for a model without `tenant_id` fails at import time, not at
  runtime on a customer's data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, cast

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models.base import Base


class TenantScopeError(RuntimeError):
    """Raised when an operation would cross a tenant boundary."""


class BaseRepository[ModelT: Base]:
    """Data access for one model, permanently bound to one tenant."""

    model: ClassVar[type[Any]]

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        """Reject repositories whose model is not tenant-scoped.

        Catching this at class-definition time means a mistake surfaces the
        first time the module is imported - including in CI - rather than as
        a silent full-table read in production.

        `abstract=True` exempts an intermediate base that exists only to share
        behaviour between real repositories and binds no model of its own. It
        must be stated explicitly: silently skipping any subclass without a
        `model` would turn a forgotten attribute - the exact mistake this
        guard exists to catch - into a pass.
        """
        super().__init_subclass__(**kwargs)
        if abstract:
            return
        model = getattr(cls, "model", None)
        if model is None:
            raise TypeError(f"{cls.__name__} must define a `model` attribute")
        if not hasattr(model, "tenant_id"):
            raise TypeError(
                f"{cls.__name__}.model ({model.__name__}) has no `tenant_id` column, "
                "so it cannot be tenant-scoped. Do not use BaseRepository for it."
            )

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> uuid.UUID:
        """The tenant every operation on this repository is confined to."""
        return self._tenant_id

    @property
    def _tenant_column(self) -> InstrumentedAttribute[uuid.UUID]:
        column: InstrumentedAttribute[uuid.UUID] = self.model.tenant_id
        return column

    @property
    def _tenant_predicate(self) -> ColumnElement[bool]:
        """The `tenant_id = :tenant` clause every statement here is built on.

        `model` is `type[Any]` because each subclass supplies a different
        one, so mypy cannot see that SQLAlchemy's overloaded `==` yields a
        SQL expression rather than a Python bool. The cast records that; the
        runtime behaviour is ordinary SQLAlchemy.
        """
        return cast("ColumnElement[bool]", self._tenant_column == self._tenant_id)

    def _scoped(self) -> Select[tuple[ModelT]]:
        """A SELECT already narrowed to this tenant.

        Every read path must start here. Building a bare `select(self.model)`
        anywhere else defeats the entire class.
        """
        statement: Select[tuple[ModelT]] = select(self.model).where(self._tenant_predicate)
        return statement

    # ---------------------------------------------------------------- reads

    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch one row by id, or None if it belongs to another tenant.

        Deliberately indistinguishable from "does not exist": telling a
        caller that an id exists but is not theirs leaks the existence of
        another tenant's data.
        """
        statement = self._scoped().where(self.model.id == entity_id)
        return self._session.execute(statement).scalar_one_or_none()

    def list(
        self,
        *criteria: ColumnElement[bool],
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[ModelT]:
        """Rows for this tenant, optionally narrowed further."""
        statement = self._scoped()
        for criterion in criteria:
            statement = statement.where(criterion)
        if offset is not None:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return self._session.execute(statement).scalars().all()

    def count(self, *criteria: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(self.model).where(self._tenant_predicate)
        for criterion in criteria:
            statement = statement.where(criterion)
        return self._session.execute(statement).scalar_one()

    def exists(self, entity_id: uuid.UUID) -> bool:
        return self.get(entity_id) is not None

    # --------------------------------------------------------------- writes

    def add(self, **values: Any) -> ModelT:
        """Create a row, stamped with this repository's tenant.

        An explicit `tenant_id` is allowed only if it matches; passing a
        different one is a bug worth surfacing rather than silently
        overwriting.
        """
        supplied = values.get("tenant_id")
        if supplied is not None and supplied != self._tenant_id:
            raise TenantScopeError(
                f"Refusing to create {self.model.__name__} for tenant {supplied!s} "
                f"from a repository bound to {self._tenant_id!s}"
            )
        values["tenant_id"] = self._tenant_id
        instance: ModelT = self.model(**values)
        self._session.add(instance)
        self._session.flush()
        return instance

    def update(self, entity_id: uuid.UUID, **values: Any) -> ModelT | None:
        """Update a row this tenant owns. Returns None if it does not."""
        if "tenant_id" in values:
            raise TenantScopeError("tenant_id cannot be reassigned through a repository")
        instance = self.get(entity_id)
        if instance is None:
            return None
        for field, value in values.items():
            setattr(instance, field, value)
        self._session.flush()
        return instance

    def delete(self, entity_id: uuid.UUID) -> bool:
        """Hard-delete a row this tenant owns. Returns whether it happened."""
        instance = self.get(entity_id)
        if instance is None:
            return False
        self._session.delete(instance)
        self._session.flush()
        return True

    def delete_all(self) -> int:
        """Remove every row belonging to this tenant. Returns the row count.

        Deletes through the ORM rather than as a bulk DELETE so that
        configured cascades and events actually run. Tenant wipes are rare
        and small enough that the extra round trip does not matter.
        """
        rows = self.list()
        for row in rows:
            self._session.delete(row)
        self._session.flush()
        return len(rows)
