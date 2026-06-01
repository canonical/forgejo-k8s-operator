import pytest

from config import ForgejoConfig, ForgejoStorageConfig

VALID_KWARGS = {
    "forgejo__log__level": "Info",
    "forgejo__server__domain": "example.com",
    "forgejo__service__default_user_visibility": "public",
    "forgejo__service__default_org_visibility": "public",
    "forgejo____run_mode": "prod",
    "forgejo__session__provider": "db",
    "forgejo__repository__signing__default_trust_model": "collaborator",
    "forgejo__repository__pull_request__default_merge_style": "merge",
}


def test_forgejo_config_valid():
    """ForgejoConfig accepts all valid values without raising."""
    ForgejoConfig(**VALID_KWARGS)


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("forgejo__log__level", "VERBOSE"),
        ("forgejo__service__default_user_visibility", "hidden"),
        ("forgejo__service__default_org_visibility", "hidden"),
        ("forgejo____run_mode", "staging"),
        ("forgejo__session__provider", "sqlite"),
        ("forgejo__repository__signing__default_trust_model", "everyone"),
        ("forgejo__repository__pull_request__default_merge_style", "cherry-pick"),
    ],
)
def test_forgejo_config_invalid(field, invalid_value):
    """ForgejoConfig raises ValueError for each invalid field value."""
    kwargs = {**VALID_KWARGS, field: invalid_value}
    with pytest.raises(ValueError):
        ForgejoConfig(**kwargs)


def test_forgejo_storage_config_dump_aliases():
    """model_dump(by_alias=True) uses FORGEJO__STORAGE__* keys."""
    cfg = ForgejoStorageConfig(
        endpoint="minio:9000",
        access_key_id="AKID",
        secret_access_key="SECRET",
        bucket="my-bucket",
        location="us-east-1",
        base_path="path/",
        use_ssl=False,
    )
    dumped = cfg.model_dump(by_alias=True)
    assert dumped == {
        "FORGEJO__STORAGE__STORAGE_TYPE": "minio",
        "FORGEJO__STORAGE__MINIO_ENDPOINT": "minio:9000",
        "FORGEJO__STORAGE__MINIO_ACCESS_KEY_ID": "AKID",
        "FORGEJO__STORAGE__MINIO_SECRET_ACCESS_KEY": "SECRET",
        "FORGEJO__STORAGE__MINIO_BUCKET": "my-bucket",
        "FORGEJO__STORAGE__MINIO_LOCATION": "us-east-1",
        "FORGEJO__STORAGE__MINIO_BASE_PATH": "path/",
        "FORGEJO__STORAGE__MINIO_USE_SSL": "false",
    }


def test_forgejo_storage_config_from_s3_info():
    """from_s3_info maps s3-credentials relation payload keys to model fields."""
    s3_info = {
        "endpoint": "minio:9000",
        "access-key": "AKID",
        "secret-key": "SECRET",
        "bucket": "my-bucket",
        "region": "us-east-1",
    }
    cfg = ForgejoStorageConfig.from_s3_info(s3_info)
    assert cfg.endpoint == "minio:9000"
    assert cfg.access_key_id == "AKID"
    assert cfg.secret_access_key == "SECRET"
    assert cfg.bucket == "my-bucket"
    assert cfg.location == "us-east-1"
    assert cfg.base_path == ""  # Default value
    assert cfg.use_ssl is True  # Default value
