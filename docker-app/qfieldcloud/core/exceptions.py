from rest_framework import status


class QFieldCloudException(Exception):
    """Generic QFieldCloud Exception"""

    code = 'unknown_error',
    message = 'QFieldcloud Unknown Error',

    def __init__(
            self,
            detail='',
            status_code=None):

        self.detail = detail

        if status_code:
            self.status_code = status_code
        elif self.status_code:
            pass
        else:
            self.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

        super().__init__(self.message)

    def __str__(self):
        return self.message


class StatusNotOkError(QFieldCloudException):
    """Raised when some parts of QFieldCloud are not working as expected"""

    code = 'status_not_ok'
    message = 'Status not ok'
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class EmptyContentError(QFieldCloudException):
    """Raised when a request doesn't contain an expected content
    (e.g. a file)"""

    code = 'empty_content'
    message = 'Empty content'
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class ObjectNotFoundError(QFieldCloudException):
    """Raised when a requested object doesn't exist
    (e.g. wrong project id into the request)"""

    code = 'object_not_found'
    message = 'Object not found'
    status_code = status.HTTP_400_BAD_REQUEST


class APIError(QFieldCloudException):
    """Raised in case of an API error"""

    code = 'api_error'
    message = 'API Error'
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class ValidationError(QFieldCloudException):
    """Raised when validation of form data or model field fails
    (e.g. wrong field in request object)"""

    code = 'validation_error'
    message = 'Validation error'
    status_code = status.HTTP_400_BAD_REQUEST


class MultipleProjectsError(QFieldCloudException):
    """Raised when the user is trying to upload more than one QGIS project
    into a QFieldCloud project"""

    code = 'multiple_projects'
    message = 'Multiple projects'
    status_code = status.HTTP_400_BAD_REQUEST


class DeltafileValidationError(QFieldCloudException):
    """Raised when a deltafile validation fails"""

    code = 'invalid_deltafile'
    message = 'Invalid deltafile'
    status_code = status.HTTP_400_BAD_REQUEST


class NoQGISProjectError(QFieldCloudException):
    """Raised when a QFieldCloud doesn't contain a QGIS project that is needed
    for the requested operation"""

    code = 'no_qgis_project'
    message = 'The project does not contain a valid QGIS project file'
    status_code = status.HTTP_400_BAD_REQUEST


class InvalidJobError(QFieldCloudException):
    """Raised when a requested job doesn't exist"""

    code = 'invalid_job'
    message = 'Invalid job'
    status_code = status.HTTP_400_BAD_REQUEST


class QGISExportError(QFieldCloudException):
    """Raised when the QGIS export of a project fails"""

    code = 'qgis_export_error'
    message = 'QGIS export failed'
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    if 'Unable to open file with QGIS' in message:
        message = 'QGIS is unable to open the QGIS project'