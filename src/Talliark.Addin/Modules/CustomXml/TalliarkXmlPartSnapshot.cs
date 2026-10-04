using System;

namespace Talliark.Addin.Modules.CustomXml
{
    /// <summary>
    /// Read-only, in-memory copy of one Talliark Custom XML part. This is a debugging
    /// view and is not a persisted contract.
    /// </summary>
    public sealed class TalliarkXmlPartSnapshot
    {
        public TalliarkXmlPartSnapshot(string namespaceUri, string xml)
        {
            NamespaceUri = namespaceUri ?? throw new ArgumentNullException(nameof(namespaceUri));
            Xml = xml ?? string.Empty;
        }

        public string NamespaceUri { get; }

        public string Xml { get; }
    }
}
