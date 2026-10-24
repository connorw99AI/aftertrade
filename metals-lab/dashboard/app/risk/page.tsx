import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/system?tab=risk" what="The risk centre" />;
}
