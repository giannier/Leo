"""Pydantic schemas shared by more than one layer.

Request and response bodies that belong to a single endpoint stay next to it in
`app/api/v1/`. What lives here is validated data that the API writes and the
prompt builder or pricing code later reads back, such as the sections of
`business_info`.
"""
