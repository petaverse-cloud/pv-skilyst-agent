import { ActionIcon, Badge, Group, Select, Stack, Switch, Text, Textarea } from "@mantine/core";
import { IconPaperclip, IconSend, IconX } from "@tabler/icons-react";
import { useRef, useState } from "react";
import { api } from "../api";

export type SkillOption = {
  skill_id: string;
  title: string;
  version: string;
  degraded: boolean;
  /** M3c (#52): v0.3 freedom shape at a glance — null when the skill
   * declares no skeleton (a plain prompt-flow skill). */
  skeleton?: {
    nodes: number;
    freedoms: { pinned: number; parameterized: number; free: number };
    free_zones: { name: string; max_nodes: number }[];
  } | null;
};

/** #81: an attachment mid-upload / attached to the next message. */
export type PendingAsset = {
  id: string;
  name: string;
  url: string;
};

export default function Composer({
  onSend,
  busy,
  dryRun,
  onDryRunChange,
  serverDryRun,
  model,
  skills,
  skill,
  onSkillChange,
  onAssetUploaded,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  dryRun: boolean;
  onDryRunChange: (value: boolean) => void;
  serverDryRun: boolean;
  model: string;
  /** M3a (#52): installed skills for the picker (GET /skills). */
  skills: SkillOption[];
  /** The selected skill id ("" = no skill, plain conversation). */
  skill: string;
  onSkillChange: (value: string) => void;
  /** #81: called with the uploaded asset so the host can wire it into the
   * run context (media pool, message). */
  onAssetUploaded?: (asset: PendingAsset) => void;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<PendingAsset | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const submit = () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    onSend(value);
  };

  // #81: read the picked file in the webview, hand it to the runtime as
  // base64 — the runtime assembles + signs the multipart POST. The webview
  // never talks to beehive directly (the production-bundle lesson).
  const pickFile = (file: File | null | undefined) => {
    setUploadError(null);
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      setUploadError(`"${file.name}" is over the 50 MiB upload limit`);
      return;
    }
    setUploading(true);
    const reader = new FileReader();
    reader.onerror = () => {
      setUploading(false);
      setUploadError(`could not read "${file.name}"`);
    };
    reader.onload = () => {
      const data_b64 = String(reader.result || "").split(",")[1] || "";
      api<PendingAsset>("/beehive/assets/upload", {
        method: "POST",
        body: {
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          data_b64,
          name: file.name,
          tags: ["desktop"],
        },
      })
        .then((asset: PendingAsset) => {
          setUploading(false);
          setPending(asset);
          onAssetUploaded?.(asset);
        })
        .catch((error: unknown) => {
          setUploading(false);
          setUploadError(String((error as Error)?.message ?? error));
        });
    };
    reader.readAsDataURL(file);
  };

  return (
    <Stack gap="xs" p="md" style={{ borderTop: "1px solid var(--mantine-color-dark-4)" }}>
      <Group gap="xs">
        <Badge size="xs" variant="light">
          {model || "model: runtime default"}
        </Badge>
        {serverDryRun ? (
          <Badge size="xs" variant="light" color="yellow" data-testid="dry-run-badge">
            dry run (server)
          </Badge>
        ) : (
          <Switch
            size="xs"
            checked={dryRun}
            onChange={(event) => onDryRunChange(event.currentTarget.checked)}
            label="dry run (no paid job)"
          />
        )}
        {/* M3a (#52): skill picker — the main selector; empty value = plain
            conversation. Degraded installs are visible with a ⚠ marker so the
            user knows WHY a skill is listed but not loadable (G4: the picker
            shows what is wrong instead of hiding it). */}
        <Select
          size="xs"
          clearable
          placeholder="skill"
          data-testid="skill-picker"
          value={skill || null}
          onChange={(value) => onSkillChange(value ?? "")}
          style={{ minWidth: 220 }}
          maxDropdownHeight={280}
          data={skills.map((s) => ({
            value: s.skill_id,
            label: `${s.degraded ? "⚠ " : ""}${s.title} · v${s.version}`,
            // M3c (#52): the v0.3 freedom shape in the dropdown — pinned ⬤,
            // parameterized ◐, free ○ (with zone allowance). Plain skills
            // (no skeleton) stay plain rows.
            description: s.skeleton
              ? `⬤ ${s.skeleton.freedoms.pinned} pinned · ◐ ${s.skeleton.freedoms.parameterized} parameterized · ○ ${s.skeleton.freedoms.free} free`
                + (s.skeleton.free_zones.length
                  ? ` · zones: ${s.skeleton.free_zones.map((z) => `${z.name} ≤${z.max_nodes}`).join(", ")}`
                  : "")
              : undefined,
          }))}
        />
        {/* #81: bring your own media — upload via the runtime proxy. */}
        <input
          ref={fileInput}
          type="file"
          accept="image/*,video/*,audio/*"
          style={{ display: "none" }}
          onChange={(event) => {
            pickFile(event.currentTarget.files?.[0]);
            event.currentTarget.value = "";
          }}
          data-testid="asset-file-input"
        />
        <ActionIcon
          size="sm"
          variant="subtle"
          onClick={() => fileInput.current?.click()}
          loading={uploading}
          disabled={busy || uploading}
          title="Attach an image, video, or audio file"
          aria-label="attach file"
          data-testid="attach-button"
        >
          <IconPaperclip size={16} />
        </ActionIcon>
        {pending ? (
          <Badge
            size="xs"
            variant="light"
            color="teal"
            pr={4}
            rightSection={
              <IconX
                size={12}
                style={{ cursor: "pointer" }}
                onClick={() => setPending(null)}
                aria-label="remove attachment"
              />
            }
            data-testid="pending-asset"
            title={pending.url}
          >
            {pending.name}
          </Badge>
        ) : null}
        <Text size="xs" c="dimmed">
          Enter sends, Shift+Enter adds a line
        </Text>
      </Group>
      {uploadError ? (
        <Text size="xs" c="red" data-testid="upload-error">
          {uploadError}
        </Text>
      ) : null}
      <Group gap="xs" align="flex-end" wrap="nowrap">
        <Textarea
          style={{ flex: 1 }}
          placeholder="Describe the shot you want, or ask about a skill…"
          autosize
          minRows={2}
          maxRows={8}
          value={text}
          disabled={busy}
          onChange={(event) => setText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          data-testid="composer"
        />
        <ActionIcon size="lg" onClick={submit} loading={busy} disabled={busy} aria-label="send">
          <IconSend size={18} />
        </ActionIcon>
      </Group>
    </Stack>
  );
}
