class AnalysisError(Exception):
    code = "ANALYSIS_ERROR"
    status_code = 500
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPdfUrlError(AnalysisError):
    code = "INVALID_PDF_URL"
    status_code = 422


class PdfDownloadError(AnalysisError):
    code = "PDF_DOWNLOAD_FAILED"
    status_code = 502
    retryable = True


class PdfTooLargeError(AnalysisError):
    code = "PDF_TOO_LARGE"
    status_code = 413


class InvalidPdfError(AnalysisError):
    code = "INVALID_PDF"
    status_code = 422


class PdfTextExtractionError(AnalysisError):
    code = "PDF_TEXT_EXTRACTION_FAILED"
    status_code = 422


class PdfTextToolUnavailableError(AnalysisError):
    code = "PDF_TEXT_TOOL_UNAVAILABLE"
    status_code = 503


class PdfTextTimeoutError(AnalysisError):
    code = "PDF_TEXT_EXTRACTION_TIMEOUT"
    status_code = 504
    retryable = True


class ProviderNotConfiguredError(AnalysisError):
    code = "MODEL_NOT_CONFIGURED"
    status_code = 503


class ProviderError(AnalysisError):
    code = "MODEL_EXTRACTION_FAILED"
    status_code = 502
    retryable = True


class AnalysisTimeoutError(AnalysisError):
    code = "ANALYSIS_TIMEOUT"
    status_code = 504
    retryable = True


class AuthenticationError(AnalysisError):
    code = "AUTHENTICATION_FAILED"
    status_code = 401


class AnalysisBusyError(AnalysisError):
    code = "ANALYSIS_SERVER_BUSY"
    status_code = 503
    retryable = True


class FundingStressUnavailableError(AnalysisError):
    code = "FUNDING_STRESS_UNAVAILABLE"
    status_code = 409
