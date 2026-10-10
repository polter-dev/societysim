import { useEffect, useState } from "react";
import { SetupForm } from "../components/SetupForm/SetupForm";
import { loadSetupAssets, type SetupAssets } from "../settings/setup";

/** Loads models_catalog.json and the setup schema, then shows the Basic/Advanced form. */
export default function NewGamePage({ catalogUrl, schemaUrl }: { catalogUrl: string; schemaUrl: string }) {
  const [assets, setAssets] = useState<SetupAssets | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    loadSetupAssets(catalogUrl, schemaUrl).then(setAssets, (err: Error) => setError(err.message));
  }, [catalogUrl, schemaUrl]);
  if (error) return <p role="alert">Could not load the model list: {error}</p>;
  if (!assets) return <p>Loading models...</p>;
  return (
    <>
      <p>Pick a lineup, then download setup.json and run the command below. Playing in the browser is not live yet.</p>
      <SetupForm assets={assets} />
    </>
  );
}
