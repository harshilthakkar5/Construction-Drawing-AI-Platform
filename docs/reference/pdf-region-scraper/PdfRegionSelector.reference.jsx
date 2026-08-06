import React, { useState, useRef, useEffect } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
// Vite: bundle the pdf.js worker as a local asset (no CDN dependency at runtime).
import pdfWorkerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerSrc;

/**
 * Renders one page of a PDF on a canvas and lets the user drag a rectangle
 * over it. On mouse-up, reports the box back as ratios (0.0 - 1.0) relative
 * to the rendered page, so the same box can be re-applied to every page of
 * the document server-side regardless of each page's actual point size.
 */
export default function PdfRegionSelector({ pdfUrl, onRegionSelected }) {
  const canvasRef = useRef(null);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);

  const [isDrawing, setIsDrawing] = useState(false);
  const [startPos, setStartPos] = useState(null);
  const [currentBox, setCurrentBox] = useState(null);
  const [displaySize, setDisplaySize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);

  // Load the PDF document whenever the file URL changes.
  useEffect(() => {
    if (!pdfUrl) return;
    let cancelled = false;
    setLoading(true);
    setCurrentBox(null);

    pdfjsLib
      .getDocument(pdfUrl)
      .promise.then((pdf) => {
        if (cancelled) return;
        setPdfDoc(pdf);
        setTotalPages(pdf.numPages);
        setCurrentPage(1);
      })
      .catch((err) => {
        console.error('Failed to load PDF:', err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [pdfUrl]);

  // Render the current page to the canvas.
  useEffect(() => {
    if (!pdfDoc || !canvasRef.current) return;
    let cancelled = false;

    pdfDoc.getPage(currentPage).then((page) => {
      if (cancelled) return;
      const canvas = canvasRef.current;
      const context = canvas.getContext('2d');
      const viewport = page.getViewport({ scale: 1.5 });

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setDisplaySize({ width: viewport.width, height: viewport.height });

      page.render({ canvasContext: context, viewport }).promise.then(() => {
        if (!cancelled) setCurrentBox(null);
      });
    });

    return () => {
      cancelled = true;
    };
  }, [pdfDoc, currentPage]);

  const getCanvasCoords = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  };

  const handleMouseDown = (e) => {
    if (!canvasRef.current) return;
    const pos = getCanvasCoords(e);
    setIsDrawing(true);
    setStartPos(pos);
    setCurrentBox({ x: pos.x, y: pos.y, w: 0, h: 0 });
  };

  const handleMouseMove = (e) => {
    if (!isDrawing || !startPos) return;
    const pos = getCanvasCoords(e);
    const x = Math.min(startPos.x, pos.x);
    const y = Math.min(startPos.y, pos.y);
    const w = Math.abs(pos.x - startPos.x);
    const h = Math.abs(pos.y - startPos.y);
    setCurrentBox({ x, y, w, h });
  };

  const handleMouseUp = () => {
    if (!isDrawing || !currentBox || displaySize.width === 0) {
      setIsDrawing(false);
      return;
    }
    setIsDrawing(false);

    // Ignore accidental tiny clicks/drags.
    if (currentBox.w < 8 || currentBox.h < 8) {
      setCurrentBox(null);
      return;
    }

    const region = {
      rel_x: Number((currentBox.x / displaySize.width).toFixed(4)),
      rel_y: Number((currentBox.y / displaySize.height).toFixed(4)),
      rel_w: Number((currentBox.w / displaySize.width).toFixed(4)),
      rel_h: Number((currentBox.h / displaySize.height).toFixed(4)),
    };

    onRegionSelected(region);
  };

  return (
    <div>
      <div className="pdf-toolbar">
        <button
          className="btn secondary"
          disabled={currentPage <= 1}
          onClick={() => setCurrentPage((p) => p - 1)}
        >
          ← Prev
        </button>
        <span>
          Page {currentPage} of {totalPages || '…'}
        </span>
        <button
          className="btn secondary"
          disabled={currentPage >= totalPages}
          onClick={() => setCurrentPage((p) => p + 1)}
        >
          Next →
        </button>
        {loading && <span style={{ fontSize: 13, color: '#666' }}>Loading PDF…</span>}
      </div>

      <div
        className="pdf-canvas-wrap"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => isDrawing && setIsDrawing(false)}
      >
        <canvas ref={canvasRef} />
        {currentBox && (
          <div
            className="selection-box"
            style={{
              left: currentBox.x,
              top: currentBox.y,
              width: currentBox.w,
              height: currentBox.h,
            }}
          />
        )}
      </div>
    </div>
  );
}
