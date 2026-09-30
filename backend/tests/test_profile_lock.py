import pytest

from app.browser.profile import ProfileInUseError, ProfileLock


def test_one_session_per_profile(tmp_path):
    first = ProfileLock(tmp_path)
    first.acquire()
    with pytest.raises(ProfileInUseError):
        ProfileLock(tmp_path).acquire()
    first.release()

    again = ProfileLock(tmp_path)
    again.acquire()
    again.release()
