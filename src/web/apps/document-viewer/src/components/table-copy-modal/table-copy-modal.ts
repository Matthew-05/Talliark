import { Modal } from "@talliark/shared";
import { resolveTableCopyPages } from "./table-copy-pages.js";
import type { TableCopyMode } from "./table-copy-pages.js";

/** Table-specific content hosted inside the frontend-wide standard Modal. */
export class TableCopyModal {
  private readonly _modal = new Modal();

  async show(sourcePage: number, totalPages: number): Promise<number[] | null> {
    const content = document.createElement("div");
    content.className = "table-copy-modal";

    const description = document.createElement("p");
    description.className = "table-copy-modal__description";
    description.textContent =
      "The selection and columns will be copied to each target page, with rows detected separately on each page. Extracted tables are stacked below the source table in Excel.";

    const fieldset = document.createElement("fieldset");
    fieldset.className = "table-copy-modal__options";
    const legend = document.createElement("legend");
    legend.className = "table-copy-modal__legend";
    legend.textContent = "Target pages";

    const belowInput = this._radio("table-copy-mode", "below");
    const belowOption = this._optionLabel(
      belowInput,
      "Copy to all below pages",
      sourcePage < totalPages - 1
        ? `Pages ${sourcePage + 2}–${totalPages}`
        : "No pages follow this page",
    );
    belowInput.checked = sourcePage < totalPages - 1;
    belowInput.disabled = sourcePage >= totalPages - 1;

    const rangeInput = this._radio("table-copy-mode", "range");
    rangeInput.checked = !belowInput.checked;
    const rangeOption = this._optionLabel(
      rangeInput,
      "Copy to range of pages",
    );

    const rangeFields = document.createElement("div");
    rangeFields.className = "table-copy-modal__range";
    const defaultStart = sourcePage < totalPages - 1 ? sourcePage + 2 : 1;
    const defaultEnd = sourcePage < totalPages - 1 ? totalPages : Math.max(1, sourcePage);
    const startInput = this._pageInput("From", defaultStart, totalPages);
    const endInput = this._pageInput("To", defaultEnd, totalPages);
    rangeFields.append(startInput.label, endInput.label);
    rangeOption.content.append(rangeFields);

    const error = document.createElement("p");
    error.className = "table-copy-modal__error";
    error.setAttribute("role", "alert");

    const updateRangeState = (): void => {
      const disabled = !rangeInput.checked;
      startInput.input.disabled = disabled;
      endInput.input.disabled = disabled;
      // A label wraps the range inputs, so `:disabled` never matches it. The
      // shared class is what carries the disabled look to the whole option.
      rangeOption.label.classList.toggle("talliark-disabled", disabled);
      error.textContent = "";
    };
    belowInput.addEventListener("change", updateRangeState);
    rangeInput.addEventListener("change", updateRangeState);
    startInput.input.addEventListener("input", () => { error.textContent = ""; });
    endInput.input.addEventListener("input", () => { error.textContent = ""; });
    updateRangeState();

    fieldset.append(legend, belowOption.label, rangeOption.label);
    content.append(description, fieldset, error);

    while (true) {
      const action = await this._modal.show({
        title: "Copy table selection",
        content,
        actions: [
          { value: "cancel", label: "Cancel" },
          { value: "copy", label: "Copy", variant: "primary", autofocus: true },
        ],
      });
      if (action !== "copy") return null;

      const mode: TableCopyMode = rangeInput.checked ? "range" : "below";
      const pages = resolveTableCopyPages(
        sourcePage,
        totalPages,
        mode,
        startInput.input.valueAsNumber,
        endInput.input.valueAsNumber,
      );
      if (pages.length > 0) return pages;

      error.textContent = mode === "below"
        ? "There are no pages below the source page."
        : `Enter a valid range from 1 to ${totalPages} that includes a page other than ${sourcePage + 1}.`;
    }
  }

  private _radio(name: string, value: TableCopyMode): HTMLInputElement {
    const input = document.createElement("input");
    input.type = "radio";
    input.name = name;
    input.value = value;
    return input;
  }

  private _optionLabel(
    input: HTMLInputElement,
    titleText: string,
    detailText?: string,
  ): { label: HTMLLabelElement; content: HTMLSpanElement } {
    const label = document.createElement("label");
    label.className = "table-copy-modal__option";
    const content = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = titleText;
    content.append(title);
    if (detailText) {
      const detail = document.createElement("small");
      detail.textContent = detailText;
      content.append(detail);
    }
    label.append(input, content);
    return { label, content };
  }

  private _pageInput(
    caption: string,
    value: number,
    totalPages: number,
  ): { label: HTMLLabelElement; input: HTMLInputElement } {
    const label = document.createElement("label");
    label.textContent = caption;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = String(totalPages);
    input.step = "1";
    input.value = String(value);
    label.append(input);
    return { label, input };
  }
}
