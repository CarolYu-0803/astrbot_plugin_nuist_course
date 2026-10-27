class CourseError(Exception):
    """A message safe to display without credentials or raw response bodies."""


class LoginExpired(CourseError):
    pass


class DataError(CourseError):
    pass


class StateBusy(CourseError):
    pass

