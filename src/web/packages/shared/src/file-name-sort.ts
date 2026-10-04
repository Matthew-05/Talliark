const FILE_NAME_COLLATOR = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: "base",
});

interface FileNameParts {
  family: string;
  extension: string;
  duplicateNumber: string | null;
}

function splitFileName(name: string): FileNameParts {
  const lastDot = name.lastIndexOf(".");
  const hasExtension = lastDot > 0;
  const stem = hasExtension ? name.slice(0, lastDot) : name;
  const extension = hasExtension ? name.slice(lastDot) : "";
  const duplicate = /^(.*) \((\d+)\)$/u.exec(stem);
  return duplicate
    ? { family: duplicate[1]!, extension, duplicateNumber: duplicate[2]! }
    : { family: stem, extension, duplicateNumber: null };
}

function compareIntegerStrings(left: string, right: string): number {
  const normalizedLeft = left.replace(/^0+(?=\d)/u, "");
  const normalizedRight = right.replace(/^0+(?=\d)/u, "");
  if (normalizedLeft.length !== normalizedRight.length) {
    return normalizedLeft.length - normalizedRight.length;
  }
  return normalizedLeft < normalizedRight ? -1 : normalizedLeft > normalizedRight ? 1 : 0;
}

/**
 * Ascending filename order with an unsuffixed original before imported duplicate
 * variants: Report.pdf, Report (1).pdf, Report (2).pdf, Report (10).pdf.
 */
export function compareFileNamesAscending(left: string, right: string): number {
  const leftParts = splitFileName(left);
  const rightParts = splitFileName(right);

  const familyOrder = FILE_NAME_COLLATOR.compare(leftParts.family, rightParts.family);
  if (familyOrder !== 0) return familyOrder;

  const extensionOrder = FILE_NAME_COLLATOR.compare(leftParts.extension, rightParts.extension);
  if (extensionOrder !== 0) return extensionOrder;

  if (leftParts.duplicateNumber === null && rightParts.duplicateNumber !== null) return -1;
  if (leftParts.duplicateNumber !== null && rightParts.duplicateNumber === null) return 1;
  if (leftParts.duplicateNumber !== null && rightParts.duplicateNumber !== null) {
    const duplicateOrder = compareIntegerStrings(
      leftParts.duplicateNumber,
      rightParts.duplicateNumber,
    );
    if (duplicateOrder !== 0) return duplicateOrder;
  }

  const collated = FILE_NAME_COLLATOR.compare(left, right);
  if (collated !== 0) return collated;
  if (left.length !== right.length) return left.length - right.length;
  return left < right ? -1 : left > right ? 1 : 0;
}
