import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/trades?tab=closed" what="Trade history" />;
}
