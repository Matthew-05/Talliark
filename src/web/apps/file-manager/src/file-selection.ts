interface FolderAssignedFile {
  id: string;
  folderId?: string;
}

/** Removes selected file IDs that are absent from the active folder filter. */
export function retainVisibleFileSelection(
  selectedIds: Set<string>,
  files: FolderAssignedFile[],
  selectedFolderId: string | null,
): boolean {
  const visibleFileIds = new Set(
    files
      .filter((file) =>
        selectedFolderId === null || (file.folderId ?? null) === selectedFolderId
      )
      .map((file) => file.id),
  );

  let changed = false;
  for (const id of selectedIds) {
    if (!visibleFileIds.has(id)) {
      selectedIds.delete(id);
      changed = true;
    }
  }
  return changed;
}
