@app.post("/api/session/{sid}/import/generic")
async def import_generic_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc
    session, import_meta = store.import_generic_bytes(sid, file.filename, contents)
    return {"session": _session_view(session), "import_summary": import_meta}