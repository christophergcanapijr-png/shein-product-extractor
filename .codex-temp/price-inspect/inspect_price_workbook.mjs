import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = "C:/Users/USER/Desktop/prompts and photos/price vinted.xlsx";
const outputDir =
  "C:/Users/USER/Documents/MY JOB AUTOMATION/shein-product-extractor/.codex-temp/price-inspect/output";

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);
await fs.mkdir(outputDir, { recursive: true });

const summary = await workbook.inspect({
  kind: "workbook,sheet,table,formula",
  include: "id,name,values,formulas",
  maxChars: 12000,
  tableMaxRows: 30,
  tableMaxCols: 15,
});
console.log(summary.ndjson);

const sheets = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 4000,
});
console.log(sheets.ndjson);

for (const sheet of workbook.worksheets.items) {
  const preview = await workbook.render({
    sheetName: sheet.name,
    autoCrop: "all",
    scale: 2,
    format: "png",
  });
  const safeName = sheet.name.replace(/[<>:"/\\|?*]/g, "_");
  await fs.writeFile(
    `${outputDir}/${safeName}.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}
