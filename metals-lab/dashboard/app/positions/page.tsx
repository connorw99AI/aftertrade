import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/trades?tab=open" what="Open positions" />;
}
