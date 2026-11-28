import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'api.dart';
import 'player.dart';

String clockLabel(int ms){
  final seconds=ms.clamp(0,86400000).toInt()~/1000;
  return '${seconds~/60}:${(seconds%60).toString().padLeft(2,'0')}';
}

class NowPlayingPage extends StatefulWidget{
  final LainPlayer player;
  final Future<void> Function(Track) feedback;
  final Future<void> Function(List<Track>) download;
  const NowPlayingPage({super.key,required this.player,required this.feedback,required this.download});
  @override State<NowPlayingPage> createState()=>_NowPlayingPageState();
}
class _NowPlayingPageState extends State<NowPlayingPage>{
  double? drag;
  int? dragTrack;
  LainPlayer get player=>widget.player;
  @override Widget build(BuildContext context)=>ValueListenableBuilder<int>(
    valueListenable:player.changes,builder:(context,_,child){
      final track=player.current;
      return Scaffold(appBar:AppBar(title:const Text('Сейчас играет'),actions:[
        IconButton(onPressed:track==null?null:()=>widget.feedback(track),icon:const Icon(Icons.more_horiz)),
      ]),body:track==null?const Center(child:Text('Выбери песню')):
      ListView(padding:const EdgeInsets.all(20),children:[
        AspectRatio(aspectRatio:1,child:DecoratedBox(decoration:BoxDecoration(
          gradient:const LinearGradient(colors:[Color(0xff3d285f),Color(0xff171421)],begin:Alignment.topLeft,end:Alignment.bottomRight),
          borderRadius:BorderRadius.circular(24)),
          child:const Center(child:Icon(Icons.album_rounded,size:140,color:Color(0xffc7b4ff))))),
        const SizedBox(height:20),
        Text(track.title,style:Theme.of(context).textTheme.headlineMedium),
        Text(track.artist,style:Theme.of(context).textTheme.titleMedium),
        if(player.offlineOnly)const Text('Офлайн · скачанный файл'),
        StreamBuilder<Duration>(stream:player.engine.positionStream,builder:(context,snapshot){
          final duration=player.engine.duration?.inMilliseconds??0;
          final value=dragTrack==track.id&&drag!=null?drag!:player.displayedPosition.toDouble();
          final pos=value.clamp(0,duration).toDouble();
          return Column(children:[
            Slider(value:pos,max:duration>0?duration.toDouble():1,
              onChanged:duration>0&&!player.busy?(v)=>setState((){drag=v;dragTrack=track.id;}):null,
              onChangeEnd:duration>0&&!player.busy?(v){setState(()=>drag=null);player.seek(Duration(milliseconds:v.round()));}:null),
            Row(mainAxisAlignment:MainAxisAlignment.spaceBetween,children:[
              Text(clockLabel(duration==0?player.displayedPosition:pos.toInt())),
              Text(duration>0?clockLabel(duration):'—'),
            ]),
          ]);
        }),
        if(player.engine.processingState==ProcessingState.loading||player.engine.processingState==ProcessingState.buffering)
          const LinearProgressIndicator(),
        Row(mainAxisAlignment:MainAxisAlignment.spaceEvenly,children:[
          IconButton(onPressed:player.busy?null:player.toggleRepeat,icon:Icon(player.repeatOne?Icons.repeat_one:Icons.repeat),
            tooltip:'Повтор трека',color:player.repeatOne?Theme.of(context).colorScheme.primary:null),
          IconButton(onPressed:player.busy?null:player.skipToPrevious,icon:const Icon(Icons.skip_previous,size:36)),
          IconButton(onPressed:player.busy?null:player.engine.playing?player.pause:player.play,
            icon:Icon(player.engine.playing?Icons.pause_circle_filled:Icons.play_circle_fill,size:68)),
          IconButton(onPressed:player.busy?null:player.skipToNext,icon:const Icon(Icons.skip_next,size:36)),
          IconButton(onPressed:()=>widget.download([track]),icon:Icon(player.cache?.isPinned(track.id)==true?Icons.download_done:Icons.download),
            tooltip:'Скачать оригинал'),
        ]),
        Row(children:[const Icon(Icons.volume_down),Expanded(child:Slider(value:player.volume,min:0,max:1,
          onChanged:(v)=>player.setVolume(v))),const Icon(Icons.volume_up)]),
        if(player.error!=null)Text(player.error!),
        const Divider(),Text('Следующие треки',style:Theme.of(context).textTheme.titleLarge),
        if(player.preparing.isNotEmpty)Text('На сервере подготавливается: ${player.preparing.length}'),
        for(var i=player.index+1;i<player.tracks.length;i++)
          upcomingRow(i,player.tracks[i]),
      ]));
    });
  Widget upcomingRow(int i,Track t)=>ListTile(key:ValueKey('$i:${t.id}'),
    leading:const Icon(Icons.music_note),title:Text(t.title),subtitle:Text(t.artist),
    onTap:(){if(i<player.tracks.length&&player.tracks[i].id==t.id)player.skipToQueueItem(i);},
    trailing:PopupMenuButton<String>(onSelected:(action){
      player.editUpcoming(i,t.id,action=='up'?-1:action=='down'?1:0);
    },itemBuilder:(_)=>[
      if(i>player.index+1)const PopupMenuItem(value:'up',child:Text('Выше')),
      if(i+1<player.tracks.length)const PopupMenuItem(value:'down',child:Text('Ниже')),
      const PopupMenuItem(value:'remove',child:Text('Убрать из очереди')),
    ]));

}
