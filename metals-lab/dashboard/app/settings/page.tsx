import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/system?tab=mt5" what="MT5 connection" />;
}
