import os
import yaml


_CFN_TAGS = [
    "!Sub", "!Ref", "!GetAtt", "!Join", "!Select", "!If",
    "!FindInMap", "!Base64", "!Cidr", "!Split", "!ImportValue",
    "!Transform", "!And", "!Or", "!Not", "!Equals",
]


def _cfn_safe_loader() -> type:
    """Return a yaml.SafeLoader subclass with no-op CFN intrinsic constructors."""
    class _Loader(yaml.SafeLoader):
        pass

    def _noop(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        return loader.construct_mapping(node, deep=True)

    for tag in _CFN_TAGS:
        _Loader.add_constructor(tag, _noop)

    return _Loader


def extract_s3key_stems(template_path: str) -> dict[str, str]:
    """Parse a CloudFormation template and return {stem: raw_s3key} for all
    Lambda S3Key entries whose value ends in '.zip'.

    Handles plain scalars, path-prefixed keys ('lambdas/foo.zip'), YAML-quoted
    values, and CFN intrinsic tags (!Sub, !Ref, etc.) via no-op constructors.
    Returns {} on any parse or I/O error.
    """
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            doc = yaml.load(f, Loader=_cfn_safe_loader())
    except Exception:
        return {}

    if not isinstance(doc, dict):
        return {}

    stems: dict[str, str] = {}
    for resource in doc.get("Resources", {}).values():
        if not isinstance(resource, dict):
            continue
        props = resource.get("Properties", {})
        if not isinstance(props, dict):
            continue
        code = props.get("Code", {})
        raw_key = (code.get("S3Key") if isinstance(code, dict) else None) or props.get("S3Key")
        if not isinstance(raw_key, str):
            continue
        raw_key = raw_key.strip("\"'")
        if not raw_key.endswith(".zip"):
            continue
        stem = os.path.splitext(os.path.basename(raw_key))[0]
        if stem:
            stems[stem] = raw_key

    return stems
