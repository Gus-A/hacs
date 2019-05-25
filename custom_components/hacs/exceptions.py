"""Custom Exceptions."""

class HacsBaseException(Exception):
    """Super basic."""
    pass

class HacsUserScrewupException(HacsBaseException):
    """Raise this when the user does something they should not do."""
    pass

class HacsNotSoBasicException(HacsBaseException):
    """Not that basic."""
    pass

class HacsDataFileMissing(HacsBaseException):
    """Raise this storage datafile is missing."""
    pass

class HacsDataNotExpected(HacsBaseException):
    """Raise this when data returned from storage is not ok."""
    pass

class HacsRepositoryInfo(HacsBaseException):
    """Raise this when repository info is missing/wrong."""
    pass

class HacsMissingManifest(HacsRepositoryInfo):
    """Raise this when manifest is missing."""
    pass


class HacsBlacklistException(HacsBaseException):
    """Raise this when the repository is currently in the blacklist."""
    pass
