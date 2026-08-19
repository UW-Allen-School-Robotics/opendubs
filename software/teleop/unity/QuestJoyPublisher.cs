using UnityEngine;

namespace RosSharp.RosBridgeClient
{
    /// <summary>
    /// Publishes one Touch controller's buttons + axes as sensor_msgs/Joy over
    /// rosbridge. Attach two instances to the RosConnector GameObject:
    ///   Controller = LTouch  topic /quest/controller/left/joy
    ///   Controller = RTouch  topic /quest/controller/right/joy
    ///
    /// axes:    [0] index trigger (0..1)  [1] hand/grip trigger (0..1)
    ///          [2] thumbstick X (-1..1)  [3] thumbstick Y (-1..1)
    /// buttons: [0] A/X  [1] B/Y  [2] thumbstick click  [3] Start/Menu (left only)
    ///
    /// The grip trigger (axes[1]) is the intended teleop DEADMAN once this drives
    /// anything — see teleop/README.md "Next steps". Proof-of-life: input only.
    /// </summary>
    public class QuestJoyPublisher : UnityPublisher<MessageTypes.Sensor.Joy>
    {
        public OVRInput.Controller Controller = OVRInput.Controller.RTouch;
        public string FrameId = "quest_controller_right";
        [Tooltip("Publish-rate cap in Hz; 0 = every FixedUpdate.")]
        public float PublishRateHz = 60f;

        private MessageTypes.Sensor.Joy message;
        private float lastPublish;

        protected override void Start()
        {
            base.Start();
            message = new MessageTypes.Sensor.Joy
            {
                header = new MessageTypes.Std.Header { frame_id = FrameId },
                axes = new float[4],
                buttons = new int[4]
            };
        }

        private void FixedUpdate()
        {
            if (PublishRateHz > 0f && Time.time - lastPublish < 1f / PublishRateHz) return;
            lastPublish = Time.time;

            Vector2 stick = OVRInput.Get(OVRInput.Axis2D.PrimaryThumbstick, Controller);
            message.axes[0] = OVRInput.Get(OVRInput.Axis1D.PrimaryIndexTrigger, Controller);
            message.axes[1] = OVRInput.Get(OVRInput.Axis1D.PrimaryHandTrigger, Controller);
            message.axes[2] = stick.x;
            message.axes[3] = stick.y;
            message.buttons[0] = OVRInput.Get(OVRInput.Button.One, Controller) ? 1 : 0;
            message.buttons[1] = OVRInput.Get(OVRInput.Button.Two, Controller) ? 1 : 0;
            message.buttons[2] = OVRInput.Get(OVRInput.Button.PrimaryThumbstick, Controller) ? 1 : 0;
            message.buttons[3] = OVRInput.Get(OVRInput.Button.Start, Controller) ? 1 : 0;

            // Header stamp left at its zero default — this ros-sharp build has no
            // RosConnector.GetTimeStamp(); proof-of-life doesn't need synced time.
            Publish(message);
        }
    }
}
