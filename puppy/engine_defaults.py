"""Node-owned starting choices, separate from a driver's native defaults."""
from __future__ import annotations

from puppy import config


def factory(driver) -> dict:
    return {"permission_mode": driver.default_permission(),
            "model": driver.default_model(), "effort": ""}


def values(driver) -> dict:
    stored = config.get("engines.defaults")[driver.key]
    return {key: stored[key] or value for key, value in factory(driver).items()}


def validate(driver, choices: dict) -> dict:
    """Refuse unsupported choices; never silently substitute a saved default."""
    result = config.normalize_engine_defaults(choices)
    result["permission_mode"] = result["permission_mode"] or driver.default_permission()
    if result["permission_mode"] not in {o["value"] for o in driver.permission_options()}:
        raise ValueError("That permission mode is not available for {}".format(driver.label))
    if not driver.allow_custom_model and result["model"] not in {
            o["value"] for o in driver.model_options()}:
        raise ValueError("That {} model is not available on this backend; choose another model".format(
            driver.label))
    if result["effort"] and result["effort"] not in {
            o["value"] for o in driver.effort_options_for_model(result["model"])}:
        raise ValueError("That effort is not available for the selected {} model".format(driver.label))
    return result


def for_session(driver, supplied: dict) -> dict:
    # Omission uses the saved choice; an explicit empty model/effort still
    # means engine default.
    choices = values(driver)
    choices.update({key: supplied[key] for key in choices if key in supplied})
    return validate(driver, choices)
