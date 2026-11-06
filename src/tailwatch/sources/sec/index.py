import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import Field

from tailwatch.sources.policy import Contract, NonEmpty


class Filing(Contract):
    indexed_cik: str = Field(pattern=r"^\d{10}$")
    company_name: NonEmpty
    form: NonEmpty
    filed_date: date
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    filename: str

    @property
    def url(self) -> str:
        return "https://www.sec.gov/Archives/" + self.filename


def parse_master_index(body: bytes) -> list[Filing]:
    try:
        lines = body.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        lines = body.decode("latin-1").splitlines()
    header = "CIK|Company Name|Form Type|Date Filed|Filename"
    try:
        start = lines.index(header) + 1
    except ValueError as error:
        raise ValueError("SEC master index header missing") from error
    records: dict[tuple[str, str], Filing] = {}
    for line in lines[start:]:
        if not line.strip() or set(line.strip()) == {"-"}:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            raise ValueError("malformed index row; refuse partial discovery")
        cik, name, form, filed, filename = (p.strip() for p in parts)
        match = re.fullmatch(r"edgar/data/(\d{1,10})/(\d{10}-\d{2}-\d{6})\.txt", filename)
        if not cik.isdigit() or len(cik) > 10 or match is None:
            raise ValueError("invalid index CIK or filing path")
        record = Filing(
            indexed_cik=cik.zfill(10),
            company_name=name,
            form=form,
            filed_date=date.fromisoformat(filed),
            accession=match[2],
            filename=filename,
        )
        key = (record.accession, record.indexed_cik)
        if key in records and records[key] != record:
            raise ValueError("conflicting duplicate accession")
        records[key] = record
    return sorted(records.values(), key=lambda record: (record.accession, record.indexed_cik))


def acceptance_time(body: bytes) -> datetime:
    matches = re.findall(rb"<ACCEPTANCE-DATETIME>(\d{14})", body)
    if len(matches) != 1:
        raise ValueError("filing requires one acceptance timestamp")
    naive = datetime.strptime(matches[0].decode("ascii"), "%Y%m%d%H%M%S")
    eastern = ZoneInfo("America/New_York")
    value = naive.replace(tzinfo=eastern)
    if value.utcoffset() != naive.replace(tzinfo=eastern, fold=1).utcoffset():
        raise ValueError("ambiguous or nonexistent Eastern acceptance timestamp")
    return value


def verify_filing(body: bytes, expected: Filing) -> datetime:
    header = body.split(b"</SEC-HEADER>", 1)[0]
    accessions = re.findall(rb"ACCESSION NUMBER:\s*(\d{10}-\d{2}-\d{6})", header)
    forms = re.findall(rb"CONFORMED SUBMISSION TYPE:\s*([^\r\n]+)", header)
    if accessions != [expected.accession.encode()] or forms != [expected.form.encode()]:
        raise ValueError("downloaded filing identity disagrees with frozen index")
    return acceptance_time(header)
