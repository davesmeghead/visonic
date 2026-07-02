"""Define Python user-defined exceptions."""


class Error(Exception):
    """Base class for other exceptions."""

    message = ""

    def __init__(self, error: str = ""):
        """Initialise."""
        if error:
            self.message = f"{self.message} - {error}"
        super().__init__(self.message)


class AlreadyGrantedError(Error):
    """Raised when trying to grant access to a user that has already been granted access."""

    message = "The user has already been granted access."


class AlreadyLinkedError(Error):
    """Raised when trying grant an already linked email address."""

    message = "The email address has already been linked to a user."


class AppIDRequiredError(Error):
    """Raised when an AppID is not provided in the API calls."""

    message = "Connection to the alarm panel failed because the app ID is missing."


class EmailRequiredError(Error):
    """Raised when the email address is missing in the authentication request."""

    message = "Authentication failed because the email address is missing."


class LoginTemporaryBlockedError(Error):
    """Raised when the password is missing in the authentication request."""

    message = "Login is temporary blocked due to too many failed login attempts."


class NewPasswordStrengthError(Error):
    """Raised when the password is missing in the authentication request."""

    message = (
        "New password is not strong enough. Please enter a complex password containing letters, "
        "digits and special characters."
    )


class NotAllowedError(Error):
    """Raised when the request is not allowed."""

    message = "The request is not allowed."


class PanelNotConnectedError(Error):
    """Raised when the API server is not connected to the alarm panel."""

    message = "Alarm panel is not connected to the API server."


class PanelSerialIncorrectError(Error):
    """Raised when an incorrect panel serial/ID number was provided in the request."""

    message = "Connection to the alarm panel failed because the panel ID is incorrect."


class PanelSerialRequiredError(Error):
    """Raised when a panel serial/ID number was not provided in the request."""

    message = "Connection to the alarm panel failed because the panel ID is missing."


class PasswordRequiredError(Error):
    """Raised when the password is missing in the authentication request."""

    message = "Request failed because the password is missing."


class ResetPasswordCodeIncorrectError(Error):
    """Raised when the password reset code obtained via email is incorrect."""

    message = "Reset password code is incorrect. Check you email or try to resend the password reset request."


class UndefinedBadRequestError(Error):
    """Raised when an undefined 400 Bad Request error occurs."""

    message = "Bad Request. Check the connection details and try again."


class UnauthorizedError(Error):
    """Raised when a 401 Client Error occurs."""

    message = "Unauthorized to access API endpoint."


class InternalServerError(Error):
    """Raised when a 500 Internal Server Error occurs."""

    message = "Internal Server Error to access API endpoint."


class UndefinedForbiddenError(Error):
    """Raised when an undefined 403 Client Error occurs."""

    message = "The request is forbidden."


class UserAuthRequiredError(Error):
    """Raised when a forbidden error occurs due to not being authenticated."""

    message = "User authentication required."


class UserCodeIncorrectError(Error):
    """Raised when the user code provided in the request is incorrect."""

    message = "Connection to the alarm panel failed because the user code is incorrect."


class UserCodeRequiredError(Error):
    """Raised when the user code provided in the request was missing."""

    message = "Connection to the alarm panel failed because the user code is missing."


class WrongUsernameOrPasswordError(Error):
    """Raised when the username or password is incorrect."""

    message = "Authentication failed because the wrong username or password was provided."


class WrongPanelSerialOrMasterUserCodeError(Error):
    """Raised when the panel serial or master user code is incorrect."""

    message = "The wrong combination of panel serial and/or master user code was provided."


class ConnectionTimeoutError(Error):
    """Raised when connection to the REST API Server timed out."""

    message = "Connection to host timed out."


class InvalidPanelIDError(Error):
    """Raised when the Panel ID is not found in the server."""

    message = "Invalid Panel ID."


class InvalidUserCodeError(Error):
    """Raised when the user code is not associated with the alarm system."""

    message = "Invalid User Code."


class LoginAttemptsLimitReachedError(Error):
    """Raised when the number of failed login attempts are too many."""

    message = "Login attempts limit reached. Please wait a few minutes and then try again."


class LoginFailedError(Error):
    """Raised when the login attempt failed."""

    message = "Login attempts failed. Please check the settings and try again. Make sure to use the master code."


class NotFoundError(Error):
    """Raised when an API endpoint is not found."""

    message = "The API endpoint was not found on the server."


class NotRestAPIError(Error):
    """Raised when connection to server is not a Rest API."""

    message = "Connection to host timed out."


class NotSupportedError(Error):
    """Raised when trying to call a method not supported in the current API version."""

    message = "Method is not supported in the selected version of the API."


class SessionTokenError(Error):
    """Raised when not authenticated with the REST API."""

    message = "Session token not found. Please log in prior to this call."

class VisonicRateLimitError(Error):
    """Raised when Login limit reached or temporary block."""

    message = "Login limit reached or temporary block."


class UnsupportedRestAPIVersionError(Error):
    """Raised when a version of the REST API is unsupported."""

    message = "Unsupported REST API version."

