from amcrawler.config import load_config
from amcrawler.geo import country_name_it, fold_countries, iso_numeric


def test_fold_pseudo_codes():
    assert fold_countries(["CT", "IT", "IA"]) == ("IT",)
    assert fold_countries(["PX"]) == ("AR", "CL")
    assert fold_countries(["ZI", "TB", "AK", "LE", "MI"]) == ("GB", "CN", "US", "ES", "PT")


def test_iso_numeric():
    assert iso_numeric("IT") == "380"
    assert iso_numeric("AF") == "004"
    assert iso_numeric("ZZ") is None


def test_country_name_it():
    assert country_name_it("JP") == "Giappone"
    assert country_name_it("ZZ") == "ZZ"


def test_load_config_defaults():
    c = load_config({})
    assert c.smtp_port == 587 and c.tz == "Europe/London" and c.dev_no_auth is False
    assert load_config({"AMC_DEV_NO_AUTH": "1"}).dev_no_auth is True
