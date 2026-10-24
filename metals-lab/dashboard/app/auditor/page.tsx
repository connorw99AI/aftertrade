import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/system?tab=auditor" what="The market auditor" />;
}
