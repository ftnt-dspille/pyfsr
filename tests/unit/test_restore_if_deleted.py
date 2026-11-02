"""restore_if_deleted: restore only a recycle-bin row (live shape: plain GET 404, $showDeleted has deletedAt)."""

from pyfsr.exceptions import ResourceNotFoundError
from pyfsr.records import RecordSet

U = "11111111-1111-1111-1111-111111111111"


class _Client:
    def __init__(self, live=None, deleted=None):
        self.live, self.deleted, self.puts = live, deleted, []

    def get(self, path, params=None):
        row = self.deleted if params and params.get("$showDeleted") else self.live
        if row is None:
            raise ResourceNotFoundError("Not Found")
        return row

    def put(self, path, data=None, params=None):
        self.puts.append(data)
        return data


def test_a_recycle_bin_row_is_restored():
    c = _Client(deleted={"uuid": U, "deletedAt": 1})
    assert RecordSet(c, "workflows").restore_if_deleted(U) is True
    assert c.puts and c.puts[0]["deletedAt"] is None


def test_a_live_row_is_left_alone():
    c = _Client(live={"uuid": U})
    assert RecordSet(c, "workflows").restore_if_deleted(U) is False and not c.puts


def test_a_hard_deleted_row_is_reported_absent():
    c = _Client()
    assert RecordSet(c, "workflows").restore_if_deleted(U) is False and not c.puts
