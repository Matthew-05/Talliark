namespace Talliark.Addin.Modules.CustomXml.Models
{
    public sealed class LinkedCell
    {
        public LinkedCell(string sheetName, string address, int trackIndex)
        {
            SheetName = sheetName;
            Address = address;
            TrackIndex = trackIndex;
        }

        public string SheetName { get; set; }

        public string Address { get; set; }

        /// <summary>
        /// Stable, monotonically-increasing integer assigned at link creation.
        /// Selects the hidden workbook name whose reference follows structural cell
        /// moves. Never reused after a link is deleted.
        /// </summary>
        public int TrackIndex { get; set; }
    }
}
