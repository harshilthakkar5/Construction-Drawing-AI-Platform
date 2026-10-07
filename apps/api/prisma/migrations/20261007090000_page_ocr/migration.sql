-- page_ocr.py: OCR of text drawn as shapes and of pasted schedule pictures.
ALTER TABLE "pages" ADD COLUMN "ocr" JSONB;
ALTER TABLE "pages" ADD COLUMN "ocrVersion" INTEGER;
