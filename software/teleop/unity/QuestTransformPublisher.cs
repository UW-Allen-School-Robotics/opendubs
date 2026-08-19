using UnityEngine;

namespace RosSharp.RosBridgeClient
{
    /// <summary>
    /// Publishes a Unity Transform (a Meta XR anchor) as geometry_msgs/PoseStamped
    /// over rosbridge. Attach one instance per tracked thing to the RosConnector
    /// GameObject and set SourceTransform to the OVRCameraRig anchor:
    ///   HMD   -> centerEyeAnchor      topic /quest/hmd/pose
    ///   left  -> LeftHandAnchor       topic /quest/controller/left/pose
    ///   right -> RightHandAnchor      topic /quest/controller/right/pose
    ///
    /// Coordinates are converted Unity(LH, Y-up) -> ROS(RH, Z-up, REP-103) via
    /// ros-sharp's Unity2Ros() extensions. Poses are reported relative to
    /// ReferenceFrame (set this to the OVRCameraRig / tracking-space root); if left
    /// null, world space is used (fine when the rig sits at the origin).
    ///
    /// Proof-of-life only — input ingest, no actuation. See teleop/README.md.
    /// </summary>
    public class QuestTransformPublisher : UnityPublisher<MessageTypes.Geometry.PoseStamped>
    {
        public Transform SourceTransform;
        public Transform ReferenceFrame;          // optional tracking-space root
        public string FrameId = "quest_tracking";
        [Tooltip("Publish-rate cap in Hz; 0 = every FixedUpdate.")]
        public float PublishRateHz = 60f;

        private MessageTypes.Geometry.PoseStamped message;
        private float lastPublish;

        protected override void Start()
        {
            base.Start();
            message = new MessageTypes.Geometry.PoseStamped
            {
                header = new MessageTypes.Std.Header { frame_id = FrameId },
                pose = new MessageTypes.Geometry.Pose
                {
                    position = new MessageTypes.Geometry.Point(),
                    orientation = new MessageTypes.Geometry.Quaternion()
                }
            };
        }

        private void FixedUpdate()
        {
            if (SourceTransform == null) return;
            if (PublishRateHz > 0f && Time.time - lastPublish < 1f / PublishRateHz) return;
            lastPublish = Time.time;

            Vector3 pos;
            Quaternion rot;
            if (ReferenceFrame != null)
            {
                pos = ReferenceFrame.InverseTransformPoint(SourceTransform.position);
                rot = Quaternion.Inverse(ReferenceFrame.rotation) * SourceTransform.rotation;
            }
            else
            {
                pos = SourceTransform.position;
                rot = SourceTransform.rotation;
            }

            // Unity2Ros() returns a coordinate-converted Vector3/Quaternion, so copy
            // the components into the ROS message fields. The header stamp is left at
            // its zero default — this ros-sharp build has no RosConnector.GetTimeStamp()
            // and proof-of-life doesn't need synced time (add a real stamp later).
            Vector3 rp = pos.Unity2Ros();
            message.pose.position.x = rp.x;
            message.pose.position.y = rp.y;
            message.pose.position.z = rp.z;
            Quaternion rq = rot.Unity2Ros();
            message.pose.orientation.x = rq.x;
            message.pose.orientation.y = rq.y;
            message.pose.orientation.z = rq.z;
            message.pose.orientation.w = rq.w;
            Publish(message);
        }
    }
}
