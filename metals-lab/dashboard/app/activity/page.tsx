import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/system?tab=log" what="Activity" />;
}
