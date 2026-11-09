import { Router, type IRouter } from "express";
import healthRouter from "./health";
import twilioInboundRouter from "./twilio-inbound";

const router: IRouter = Router();

router.use(healthRouter);
router.use(twilioInboundRouter);

export default router;
