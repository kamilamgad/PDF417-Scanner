from app.parser import parse_aamva_payload


def test_parse_standard_payload_dates_and_fields():
    payload = (
        "@\n"
        "ANSI 636026080102DL00410288ZA03290015DLDAQD12345678\n"
        "DCSDOE\n"
        "DACJANE\n"
        "DADANN\n"
        "DBB19860504\n"
        "DBD20210115\n"
        "DBA20290115\n"
        "DBC2\n"
        "DAG123 MAIN ST\n"
        "DAIANYTOWN\n"
        "DAJny\n"
        "DAK12345-6789\n"
        "DCF000123456789\n"
    )

    result = parse_aamva_payload(payload)

    assert result.fields.firstName == "JANE"
    assert result.fields.middleName == "ANN"
    assert result.fields.lastName == "DOE"
    assert result.fields.dateOfBirth == "1986-05-04"
    assert result.fields.issueDate == "2021-01-15"
    assert result.fields.expirationDate == "2029-01-15"
    assert result.fields.gender == "F"
    assert result.fields.licenseNumber == "D12345678"
    assert result.fields.documentNumber == "000123456789"
    assert result.fields.state == "NY"
    assert result.fields.postalCode == "123456789"


def test_parse_mmddyyyy_date_format_and_male_gender():
    payload = "DBB05041986\nDBC1\nDAK90210\n"
    result = parse_aamva_payload(payload)

    assert result.fields.dateOfBirth == "1986-05-04"
    assert result.fields.gender == "M"
    assert result.fields.postalCode == "90210"


def test_parse_handles_unknown_gender_and_missing_fields():
    payload = "DCSSMITH\nDACJOHN\nDBC9\n"
    result = parse_aamva_payload(payload)

    assert result.fields.firstName == "JOHN"
    assert result.fields.lastName == "SMITH"
    assert result.fields.gender == "U"
    assert result.raw_fields["DBC"] == "9"


def test_parse_maps_driver_class_from_dca():
    payload = "DACJANE\nDCSDOE\nDCAF\nDBC2\n"
    result = parse_aamva_payload(payload)

    assert result.fields.driverClass == "F"
    assert result.fields.gender == "F"


def test_parse_prefers_daq_over_dck_for_license_number():
    payload = "DAQK540066566450\nDCK1006017478\n"
    result = parse_aamva_payload(payload)

    assert result.fields.licenseNumber == "K540066566450"


def test_parse_moves_class_like_dbc_to_driver_class():
    payload = "DACJANE\nDCSDOE\nDBCD\n"
    result = parse_aamva_payload(payload)

    assert result.fields.driverClass == "D"
    assert result.fields.gender is None


def test_parse_handles_literal_lf_markers_in_payload():
    payload = "DCSKAMIL<LF>DDEN<LF>DACAMGAD<LF>DADM<LF>DCA C<LF>DBB19980616<LF>"
    result = parse_aamva_payload(payload)

    assert result.fields.lastName == "KAMIL"
    assert result.fields.firstName == "AMGAD"
    assert result.fields.middleName == "M"
    assert result.fields.driverClass == "C"
    assert result.fields.dateOfBirth == "1998-06-16"


def test_parse_full_name_from_daa_and_alias_codes():
    payload = (
        "DAADOE,JOHN,ALAN\n"
        "DCKX99887766\n"
        "DBB05041986\n"
        "DAG456 OAK AVE\n"
        "DAISEATTLE\n"
        "DAJwa\n"
        "DAK98101\n"
    )
    result = parse_aamva_payload(payload)

    assert result.fields.firstName == "JOHN"
    assert result.fields.middleName == "ALAN"
    assert result.fields.lastName == "DOE"
    assert result.fields.licenseNumber == "X99887766"
    assert result.fields.dateOfBirth == "1986-05-04"
    assert result.fields.state == "WA"
