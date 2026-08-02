/**
 * VideoSheet.gs
 * Helpers for the "videos" sheet tab.
 * Tracks source videos stored in the Drive input folder.
 *
 * Columns (1-based):
 *   A  file_id     — Google Drive file ID of the source video
 *   B  file_name   — original filename (e.g. "interview.mp4")
 *   C  status      — 'pending' | 'processing' | 'ready' | 'error'
 *   D  clip_count  — number of clips generated (0 until ready)
 *   E  created_at  — ISO timestamp when row was added
 *   F  error_msg   — error message if status = 'error'
 */

var VD_COL_FILE_ID    = 1;
var VD_COL_FILE_NAME  = 2;
var VD_COL_STATUS     = 3;
var VD_COL_CLIP_COUNT = 4;
var VD_COL_CREATED_AT = 5;
var VD_COL_ERROR_MSG  = 6;

// ============================================================
// Sheet bootstrap
// ============================================================

/**
 * Returns the videos sheet, creating it with headers if it doesn't exist.
 * @returns {GoogleAppsScript.Spreadsheet.Sheet}
 */
function getOrCreateVideoSheet() {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName('videos');
  if (!sheet) {
    sheet = ss.insertSheet('videos');
    sheet.getRange(1, 1, 1, 6).setValues([[
      'file id', 'file name', 'status', 'clip count', 'created at', 'error'
    ]]);
    sheet.setFrozenRows(1);
    sheet.setColumnWidth(VD_COL_FILE_ID,    280);
    sheet.setColumnWidth(VD_COL_FILE_NAME,  220);
    sheet.setColumnWidth(VD_COL_STATUS,      90);
    sheet.setColumnWidth(VD_COL_CLIP_COUNT,  90);
    sheet.setColumnWidth(VD_COL_CREATED_AT, 180);
    sheet.setColumnWidth(VD_COL_ERROR_MSG,  260);
  }
  return sheet;
}

// ============================================================
// Read helpers
// ============================================================

/**
 * Returns all video rows as an array of objects.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @returns {Array<Object>}
 */
function getAllVideos(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  return sheet.getRange(2, 1, lastRow - 1, 6).getValues().map(function(row, i) {
    return {
      rowIndex:  i + 2,
      fileId:    String(row[VD_COL_FILE_ID    - 1] || ''),
      fileName:  String(row[VD_COL_FILE_NAME  - 1] || ''),
      status:    String(row[VD_COL_STATUS     - 1] || 'pending'),
      clipCount: Number(row[VD_COL_CLIP_COUNT - 1] || 0),
      createdAt: String(row[VD_COL_CREATED_AT - 1] || ''),
      errorMsg:  String(row[VD_COL_ERROR_MSG  - 1] || ''),
    };
  });
}

/**
 * Returns true if a video with the given file ID already exists in the sheet.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {string} fileId
 * @returns {boolean}
 */
function isVideoAlreadyTracked(sheet, fileId) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return false;
  var ids = sheet.getRange(2, VD_COL_FILE_ID, lastRow - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) {
    if (String(ids[i][0]).trim() === fileId.trim()) return true;
  }
  return false;
}

// ============================================================
// Write helpers
// ============================================================

/**
 * Adds a new video row with status = 'pending'.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {string} fileId
 * @param {string} fileName
 * @returns {number}  1-based row index
 */
function addVideoRow(sheet, fileId, fileName) {
  var rowIndex = sheet.getLastRow() + 1;
  sheet.getRange(rowIndex, 1, 1, 6).setValues([[
    fileId, fileName, 'pending', 0, new Date().toISOString(), ''
  ]]);
  return rowIndex;
}

/**
 * Updates the status, clip count, and error message for a video row.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} rowIndex
 * @param {string} status
 * @param {number} [clipCount]
 * @param {string} [errorMsg]
 */
function updateVideoRow(sheet, rowIndex, status, clipCount, errorMsg) {
  sheet.getRange(rowIndex, VD_COL_STATUS).setValue(status);
  if (clipCount !== undefined && clipCount !== null) {
    sheet.getRange(rowIndex, VD_COL_CLIP_COUNT).setValue(clipCount);
  }
  if (errorMsg !== undefined && errorMsg !== null) {
    sheet.getRange(rowIndex, VD_COL_ERROR_MSG).setValue(errorMsg);
  }
}

/**
 * Deletes a video row by its 1-based row index.
 * @param {GoogleAppsScript.Spreadsheet.Sheet} sheet
 * @param {number} rowIndex
 */
function deleteVideoRow(sheet, rowIndex) {
  sheet.deleteRow(rowIndex);
}
