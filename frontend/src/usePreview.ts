import { useEffect, useState } from "react";
import { Api, Facts, GeneratedFile, Options, TokenReport } from "./api";

/** The live preview: `options` rendered for `facts` and the agent, 250ms after
 *  the last change, with any answer for an older request dropped.
 *
 *  `previewToken` is what the bundle does with the credential, straight from
 *  the server; it goes with the files, so it is cleared with them.
 *  `options` must keep its identity when nothing changed, or every render
 *  re-generates. */
export function usePreview(api: Api, facts: Facts | null, shipId: string | null,
                           options: Options) {
  const [files, setFiles] = useState<GeneratedFile[]>([]);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [previewToken, setPreviewToken] = useState<TokenReport | null>(null);

  useEffect(() => {
    if (!facts) { setFiles([]); setPreviewToken(null); return; }
    // A bundle is generated for an agent; without one there is nothing to show.
    if (!shipId) { setFiles([]); setPreviewToken(null); setGenErr(null); return; }
    let live = true;
    const timer = window.setTimeout(async () => {
      try {
        const r = await api.generate(facts, { ...options, ship_id: shipId });
        if (!live) return;
        setFiles(r.files);
        setPreviewToken(r.token);
        setGenErr(null);
        // Keep the open file open while it is still generated.
        setActiveFile((a) => (a && r.files.some((f) => f.name === a)
          ? a : r.files[0]?.name ?? null));
      } catch (e) { if (live) setGenErr(String((e as Error).message)); }
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [api, facts, options, shipId]);

  return { files, genErr, setGenErr, activeFile, setActiveFile, previewToken };
}
