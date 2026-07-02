from fastapi import status as http_status
from fastapi.responses import JSONResponse


def api_response(status: str = "success", data: dict | None = None, message: str = ""):
    return {"status": status, "data": data or {}, "message": message}


def json_response(
    status: str = "success",
    data: dict | None = None,
    message: str = "",
    status_code: int = http_status.HTTP_200_OK,
):
    return JSONResponse(
        status_code=status_code,
        content=api_response(status=status, data=data, message=message),
    )
