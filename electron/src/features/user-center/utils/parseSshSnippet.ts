/** Parse an AutoDL / ssh paste blob into connection fields. */

export type ParsedSshSnippet = {
  host?: string;
  port?: number;
  user?: string;
  ssh_password?: string;
  ssh_key?: string;
};

const USER_HOST_RE = /(?:^|[\s"'`])([A-Za-z0-9._-]+)@([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9])/;
const PORT_FLAG_RE = /(?:^|\s)-p\s*(\d{2,5})(?:\s|$)/i;
const PORT_COLON_RE = /@[^:\s]+:(\d{2,5})(?:\s|$)/;
const KEY_FLAG_RE = /(?:^|\s)-i\s+(\S+)/;
const SSHPASS_RE = /sshpass\s+-p\s*['"]?([^\s'"]+)/i;
const PASSWORD_LABEL_RE = /(?:密码|passwd|password)\s*[：:=\s]\s*(\S+)/i;
const PASSWORD_GLUE_RE = /密码([A-Za-z0-9_!@#$%^&*.-]{4,})/;

export function suggestAutodlNodeId(nameOrHost: string): string {
  const slug = (nameOrHost || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!slug) return "autodl-1";
  return slug.startsWith("autodl") ? slug : `autodl-${slug}`;
}

export function parseSshSnippet(raw: string): ParsedSshSnippet {
  const text = (raw || "").trim();
  if (!text) return {};

  const out: ParsedSshSnippet = {};
  const userHost = text.match(USER_HOST_RE);
  if (userHost) {
    out.user = userHost[1];
    out.host = userHost[2];
  }

  const portFlag = text.match(PORT_FLAG_RE);
  const portColon = text.match(PORT_COLON_RE);
  const portRaw = portFlag?.[1] || portColon?.[1];
  if (portRaw) {
    const port = Number(portRaw);
    if (port > 0 && port <= 65535) out.port = port;
  }

  const key = text.match(KEY_FLAG_RE);
  if (key?.[1] && key[1] !== "-p") out.ssh_key = key[1];

  const sshpass = text.match(SSHPASS_RE);
  const labeled = text.match(PASSWORD_LABEL_RE);
  const glued = text.match(PASSWORD_GLUE_RE);
  const password = sshpass?.[1] || labeled?.[1] || glued?.[1];
  if (password) out.ssh_password = password.replace(/^['"]|['"]$/g, "");

  return out;
}
