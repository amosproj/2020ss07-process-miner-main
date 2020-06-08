"""
Module used for retrieving log entries and storing them for later analysis.
"""
import csv
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Iterable, Sequence, Tuple

from . import graylog_access
from .graylog_access import GraylogAccess

log = logging.getLogger(__name__)

TIMESTAMP_FILENAME = 'last_included_timestamp'
EXPORTED_FIELDS = ['correlationId', 'timestamp', 'message']
ADDED_FIELDS = ['approach', 'consent']
APPROACHES_TYPES = \
    ['redirect', 'embedded', 'OAuth', 'Decoupled', 'not available']
APPROACHES_SIGNAL_WORDS = ['approach=REDIRECT', 'approach=EMBEDDED',
                           'approach=OAUTH', 'approach=DECOUPLED']
CONSENT_TYPES = ['GET_ACCOUNTS', 'GET_TRANSACTIONS', 'not available']
CONSENT_SIGNAL_WORDS = ['GET_ACCOUNTS', 'GET_TRANSACTIONS',
                        'get account list', 'get transaction list']


def _get_advanced_timestamp(timestamp: datetime) -> datetime:
    return timestamp + timedelta(milliseconds=1)


def _read_timestamp(path: Path) -> str:
    with path.open('r') as file:
        return file.readline()


def _write_timestamp(timestamp: str, path: Path) -> None:
    with path.open("w") as timestamp_file:
        timestamp_file.write(timestamp)


def _sanitize_filename(filename: str) -> str:
    #  Windows does not support ':' as part of filenames as it is a
    #  reserved character. There are more invalid characters but for now
    #  this should do.
    return filename.replace(':', '_')


def _add_approach(grouped_dict) -> None:
    """
    add approach value to grouped dictionary before convert to csv
    """
    approach_list = defaultdict(list)
    for (correlation_id, log_entries) in grouped_dict.items():
        i = 0
        j = 0
        unlabeled = True
        for _ in enumerate(log_entries):
            # searching for signal words in message
            wholemessage = log_entries[i]['message']
            result_redirect = wholemessage.find(APPROACHES_SIGNAL_WORDS[0])
            result_embedded = wholemessage.find(APPROACHES_SIGNAL_WORDS[1])
            result_oauth = wholemessage.find(APPROACHES_SIGNAL_WORDS[2])
            result_decoupled = wholemessage.find(APPROACHES_SIGNAL_WORDS[3])

            # if signal word is found it will be added to a approach dictionary
            # identified by correlation id
            if result_redirect != -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[0]
                unlabeled = False
            if result_embedded != -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[1]
                unlabeled = False
            if result_redirect == -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[4]
            if result_embedded == -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[4]

            if result_oauth != -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[2]
                unlabeled = False
            if result_decoupled != -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[3]
                unlabeled = False
            if result_oauth == -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[4]
            if result_decoupled == -1 and unlabeled:
                approach_list[correlation_id] = APPROACHES_TYPES[4]
            i = i + 1

        # add to each row the approach value
        for _ in enumerate(log_entries):
            log_entries[j]['approach'] = approach_list[correlation_id]
            j = j + 1
    return grouped_dict


def _add_consent(grouped_dict) -> None:
    """
    add consent value to grouped dictionary before convert to csv
    """
    correlation_id = grouped_dict.items()
    consent_list = defaultdict(list)
    for (correlation_id, log_entries) in grouped_dict.items():
        i = 0
        # searching for signal words in message
        for _ in enumerate(log_entries):
            wholemessage = log_entries[i]['message']
            result_get_accounts1 = wholemessage.find(CONSENT_SIGNAL_WORDS[0])
            result_get_transactions1 = \
                wholemessage.find(CONSENT_SIGNAL_WORDS[1])
            result_get_accounts2 = wholemessage.find(CONSENT_SIGNAL_WORDS[2])
            result_get_transactions2 = \
                wholemessage.find(CONSENT_SIGNAL_WORDS[3])
            notlabeled = True

            # if a signal word is found, the consent value is added to row,
            # otherwise the consent value is 'not available'
            if result_get_accounts1 != -1 or result_get_accounts2 != -1:
                consent_list[correlation_id] = CONSENT_TYPES[0]
                log_entries[i]['consent'] = consent_list[correlation_id]
                notlabeled = False
            if result_get_transactions1 != -1 or \
                    result_get_transactions2 != -1:
                consent_list[correlation_id] = CONSENT_TYPES[1]
                log_entries[i]['consent'] = consent_list[correlation_id]
                notlabeled = False

            if result_get_accounts1 == -1 and result_get_accounts2 == -1 \
                    and notlabeled:
                consent_list[correlation_id] = CONSENT_TYPES[2]
                log_entries[i]['consent'] = consent_list[correlation_id]
            if result_get_transactions1 == -1 \
                    and result_get_transactions2 == -1 and notlabeled:
                consent_list[correlation_id] = CONSENT_TYPES[2]
                log_entries[i]['consent'] = consent_list[correlation_id]

            i = i + 1

    return correlation_id


class LogRetriever:
    """
    Class used for retrieving and storing log entries.
    """

    def __init__(self, url: str, api_token: str, target_dir: str):
        self.graylog_access = GraylogAccess(url, api_token)
        self.target_dir = Path(target_dir)

    def __str__(self) -> str:
        return f'{self.__class__.__name__} [' \
               f'graylog_access <{self.graylog_access}>]'

    def retrieve_logs(self) -> None:
        """
        Retrieves logs from the configured Graylog instance. Logs are stored
        in the configured directory grouped by their correlationID in separate
        CSV files.
        """
        self._prepare_target_dir()

        last_retrieved_timestamp = self._load_last_included_timestamp()
        first_timestamp = _get_advanced_timestamp(last_retrieved_timestamp)
        lines = self.graylog_access.get_log_entries(
            first_timestamp, EXPORTED_FIELDS)

        if not lines:
            log.info("no (new) log entries found")
            return

        fields, grouped_lines, last_timestamp = self._process_csv_lines(lines)
        # here adding values for approach and consent before store as csv
        _add_approach(grouped_lines)
        _add_consent(grouped_lines)
        self._store_logs_as_csv(grouped_lines, fields)
        self._store_last_included_timestamp(last_timestamp)

    def _prepare_target_dir(self) -> None:
        log.info('preparing target directory "%s"', self.target_dir)
        if not self.target_dir.exists():
            log.info('creating missing target directory (and parents)...')
            self.target_dir.mkdir(parents=True, exist_ok=True)

    def _load_last_included_timestamp(self) -> datetime:
        timestamp_path = self.target_dir.joinpath(TIMESTAMP_FILENAME)
        if timestamp_path.exists() and timestamp_path.is_file():
            log.info('reading last included timestamp from file "%s"',
                     timestamp_path)
            timestamp = _read_timestamp(timestamp_path)
            if graylog_access.timestamp_format_is_valid(timestamp):
                log.info('timestamp of last retrieved log entry: "%s"',
                         timestamp)
                return graylog_access.get_datetime_from_timestamp(timestamp)
            log.error('invalid timestamp format "%s"...', timestamp)
        else:
            log.info("information about last included timestamp not found in "
                     "target directory...")

        default_time = datetime.fromtimestamp(0)
        log.info("...using default timestamp '%s'", default_time)
        return default_time

    def _store_last_included_timestamp(self, timestamp) -> None:
        timestamp_path = self.target_dir.joinpath(TIMESTAMP_FILENAME)
        log.info("storing timestamp of last log entry to file '%s'",
                 timestamp_path)
        _write_timestamp(timestamp, timestamp_path)

    @staticmethod
    def _process_csv_lines(
            lines: List[str]) -> Tuple[Sequence[str],
                                       Dict[str, List[Dict[str, str]]], str]:
        reader = csv.DictReader(lines)
        sorted_list = sorted(reader, key=lambda row: row['timestamp'],
                             reverse=False)
        grouped_lines = LogRetriever._group_by_correlation_id(sorted_list)
        timestamp_of_last_entry = sorted_list[-1]['timestamp']

        # adds the additional fieldnames to original fieldnames
        fields = reader.fieldnames + ADDED_FIELDS

        return fields, grouped_lines, timestamp_of_last_entry

    @staticmethod
    def _group_by_correlation_id(
            lines: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
        grouped_lines = defaultdict(list)
        for line in lines:
            correlation_id = line['correlationId']
            if not correlation_id:
                log.info("omitting row with missing correlationId %s", line)
                continue
            grouped_lines[correlation_id].append(line)
        return grouped_lines

    def _store_logs_as_csv(self, grouped_dict,
                           fieldnames: Iterable[str]) -> None:
        for (correlation_id, log_entries) in grouped_dict.items():
            first_timestamp = log_entries[0]['timestamp']
            filename = f"{first_timestamp}_{correlation_id}.csv"
            file_path = self.target_dir.joinpath(_sanitize_filename(filename))
            log.info("storing process with correlation_id '%s' in file '%s'",
                     correlation_id, file_path)
            with file_path.open("w", newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames)
                writer.writeheader()
                writer.writerows(log_entries)
