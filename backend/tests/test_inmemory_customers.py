from app.repositories import customers_repo


def test_inmemory_customers_are_tenant_scoped_by_phone():
    # Clear existing in-memory state.
    customers_repo._by_id.clear()
    customers_repo._by_phone.clear()
    customers_repo._by_business.clear()

    phone = "+15550001000"

    # Same phone for two different tenants should produce two distinct customers.
    c1 = customers_repo.upsert(
        name="Tenant A Customer", phone=phone, business_id="tenant_a"
    )
    c2 = customers_repo.upsert(
        name="Tenant B Customer", phone=phone, business_id="tenant_b"
    )

    assert c1.id != c2.id
    assert c1.business_id == "tenant_a"
    assert c2.business_id == "tenant_b"

    # Lookups by business_id should resolve the correct record.
    lookup_a = customers_repo.get_by_phone(phone, business_id="tenant_a")
    lookup_b = customers_repo.get_by_phone(phone, business_id="tenant_b")

    assert lookup_a is not None and lookup_a.id == c1.id
    assert lookup_b is not None and lookup_b.id == c2.id

